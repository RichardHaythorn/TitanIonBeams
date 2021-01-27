from __future__ import unicode_literals

import csv
import time

import matplotlib
import scipy.signal
from astropy.modeling import models, fitting
from cassinipy.caps.mssl import *
from cassinipy.caps.spice import *
from cassinipy.caps.util import *
from cassinipy.misc import *
from cassinipy.spice import *
from scipy.signal import peak_widths
import pandas as pd
import spiceypy as spice
from astropy.modeling import models, fitting

from lmfit import CompositeModel, Model
from lmfit.models import GaussianModel
from lmfit import Parameters
from lmfit import Minimizer

import datetime
from util import *

# Loading Kernels
if spice.ktotal('spk') == 0:
    for file in glob.glob("spice/**/*.*", recursive=True):
        spice.spiceypy.furnsh(file)
    count = spice.ktotal('ALL')
    print('Kernel count after load:        {0}\n'.format(count))

ibscalib = readsav('calib\\ibsdisplaycalib.dat')
elscalib = readsav('calib\\geometricfactor.dat')
sngcalib = readsav('calib\\sngdisplaycalib.dat')

Af = 0.33e-4
MCPefficiency = 0.05
ELS_FWHM = 0.167
IBS_FWHM = 0.014

AMU = scipy.constants.physical_constants['atomic mass constant'][0]
AMU_eV = scipy.constants.physical_constants['atomic mass unit-electron volt relationship'][0]
e = scipy.constants.physical_constants['atomic unit of charge'][0]
e_mass = scipy.constants.physical_constants['electron mass'][0]
e_mass_eV = scipy.constants.physical_constants['electron mass energy equivalent in MeV'][0] * 1e6
c = scipy.constants.physical_constants['speed of light in vacuum'][0]
k = scipy.constants.physical_constants['Boltzmann constant'][0]

filedates = {"t16": "22-jul-2006", "t17": "07-sep-2006",
             "t20": "25-oct-2006", "t21": "12-dec-2006", "t25": "22-feb-2007", "t26": "10-mar-2007",
             "t27": "26-mar-2007",
             "t28": "10-apr-2007", "t29": "26-apr-2007",
             "t30": "12-may-2007", "t32": "13-jun-2007",
             "t42": "25-mar-2008", "t46": "03-nov-2008", "t47": "19-nov-2008"}

IBS_fluxfitting_dict = {"mass28_":{"sigma":0.4,"amplitude":[]},
                        "mass40_":{"sigma":0.5,"amplitude":[]},
                        "mass53_":{"sigma":0.5,"amplitude":[]},
                        "mass66_":{"sigma":0.6,"amplitude":[]}, \
                        "mass78_":{"sigma":0.7,"amplitude":[]}, \
                        "mass91_":{"sigma":0.8,"amplitude":[]}}


def energy2mass(energyarray, spacecraftvelocity, ionvelocity, spacecraftpotential, iontemperature=150, charge=1):
    massarray = (2 * (energyarray * e + (spacecraftpotential * charge * e) - 8 * k * iontemperature)) / (
            ((spacecraftvelocity + ionvelocity) ** 2) * AMU)
    return massarray


def mass2energy(massarray, spacecraftvelocity, ionvelocity, spacecraftpotential, iontemperature=150, charge=1):
    energyarray = (0.5 * massarray * ((spacecraftvelocity + ionvelocity) ** 2) * AMU - (
            spacecraftpotential * charge * e) + 8 * k * iontemperature) / e
    return energyarray


def total_fluxgaussian(xvalues, yvalues, masses, tempcassini_speed, windspeed, LPvalue, temperature,charge,FWHM):
    gaussmodels = []
    pars = Parameters()
    pars.add('windspeed', value=windspeed, min=-400, max=400)
    pars.add('scp', value=LPvalue, min=LPvalue-0.5, max=LPvalue+0.5)
    pars.add('temp', value=temperature, min=130, max=170)
    pars.add('spacecraftvelocity', value=tempcassini_speed)
    pars['spacecraftvelocity'].vary = False
    pars['temp'].vary = False

    pars.add('e', value=e)
    pars.add('AMU', value=AMU)
    pars.add('k', value=k)
    pars.add('charge', value=charge)
    pars['e'].vary = False
    pars['AMU'].vary = False
    pars['k'].vary = False
    pars['charge'].vary = False

    for masscounter, mass in enumerate(masses):
        tempprefix = "mass" + str(mass) + '_'
        gaussmodels.append(GaussianModel(prefix=tempprefix))
        pars.add(tempprefix, value=mass)
        pars.update(gaussmodels[-1].make_params())
        print(mass)
        temppeakflux = peakflux(mass, pars['spacecraftvelocity'], pars['windspeed'], pars['scp'], pars['temp'], charge=charge)
        print("Init Flux", temppeakflux)
        beamwidth = np.sqrt((2* k * temperature) / (mass * AMU))/(tempcassini_speed+pars['windspeed'])*temppeakflux
        peakfluxexpr = '(0.5*(' + tempprefix + '*AMU)*((spacecraftvelocity + windspeed)**2) - scp*e*charge + 8*k*temp)/e'
        #beamwidthexpr = tempprefix + 'center*(((2*k*temp)/(' + tempprefix +'*AMU))**0.5)/(2.35482*(spacecraftvelocity+windspeed))'

        pars[tempprefix].set(value=mass, min=mass - 1, max=mass + 1)
        pars[tempprefix + 'center'].set(
            value=peakflux(mass, tempcassini_speed, pars['windspeed'], pars['scp'], pars['temp'], charge=charge),
            expr=peakfluxexpr)

        #pars[tempprefix + 'sigma'].set(value=(temppeakflux*0.05),max=(temppeakflux*0.1))
        sigmaval = IBS_fluxfitting_dict[tempprefix]['sigma']
        pars[tempprefix + 'sigma'].set(value=sigmaval,min=0.5*sigmaval,max=2*sigmaval)
        pars[tempprefix + 'amplitude'].set(value=np.mean(yvalues),min=min(yvalues))

    for counter, model in enumerate(gaussmodels):
        if counter == 0:
            mod = model
        else:
            mod = mod + model

    init = mod.eval(pars, x=xvalues)
    out = mod.fit(yvalues, pars, x=xvalues)

    print(out.fit_report(min_correl=0.7))

    return out

def titan_linearfit_temperature(altitude):
    if altitude > 1150:
        temperature = 110 + 0.26*(altitude-1200)
    else:
        temperature = 133 - 0.12 * (altitude - 1100)
    return temperature

#[28, 29, 39, 41, 52, 54, 65, 66, 76, 79, 91]
def IBS_fluxfitting(ibsdata, tempdatetime, titanaltitude, ibs_masses=[28, 40, 53, 66, 78, 91], lpvalue=-0.3):
    et = spice.datetime2et(tempdatetime)
    state, ltime = spice.spkezr('CASSINI', et, 'IAU_TITAN', 'NONE', 'TITAN')
    cassini_speed = np.sqrt((state[3]) ** 2 + (state[4]) ** 2 + (state[5]) ** 2) * 1e3
    slicenumber = CAPS_slicenumber(ibsdata, tempdatetime)
    lowerenergyslice = CAPS_energyslice("ibs", 4-lpvalue, 4-lpvalue)[0]
    upperenergyslice = CAPS_energyslice("ibs", 17-lpvalue, 17-lpvalue)[0]
    lpdata = read_LP_V1(ibsdata['flyby'])
    lp_timestamps = [datetime.datetime.timestamp(d) for d in lpdata['datetime']]
    lpvalue = np.interp(datetime.datetime.timestamp(tempdatetime),lp_timestamps,lpdata['SPACECRAFT_POTENTIAL'])
    print("interp lpvalue", lpvalue)

    windspeed = 0
    temperature = titan_linearfit_temperature(titanaltitude)

    dataslice = ibsdata['ibsdata'][lowerenergyslice:upperenergyslice, 1, slicenumber]


    # stepplotax.plot(elscalib['earray'],smoothedcounts_full,color='k')
    # plt.show()

    x = ibscalib['ibsearray'][lowerenergyslice:upperenergyslice]
    out = total_fluxgaussian(x, dataslice, ibs_masses, cassini_speed, windspeed, 0, temperature,charge=1,FWHM=IBS_FWHM)
    comps = out.eval_components(x=x)

    stepplotfig, stepplotax = plt.subplots()
    stepplotax.step(ibscalib['ibspolyearray'][lowerenergyslice:upperenergyslice], dataslice, where='post', label=elsdata['flyby'], color='k')
    stepplotax.errorbar(x, dataslice, yerr=[np.sqrt(i) for i in dataslice], color='k', fmt='none')
    stepplotax.set_xlim(3, 19)
    stepplotax.set_ylim(bottom=1e4)
    stepplotax.set_yscale("log")
    stepplotax.set_ylabel("DEF [$m^{-2} s^{1} str^{-1} eV^{-1}$]", fontsize=20)
    stepplotax.set_xlabel("Energy (Pre-correction) [eV/q]", fontsize=20)
    stepplotax.tick_params(axis='both', which='major', labelsize=15)
    stepplotax.grid(b=True, which='major', color='k', linestyle='-', alpha=0.5)
    stepplotax.grid(b=True, which='minor', color='k', linestyle='--', alpha=0.25)
    stepplotax.minorticks_on()
    stepplotax.set_title(
        "Histogram of " + ibsdata['flyby'].upper() + " IBS data from " + ibsdata['times_utc_strings'][slicenumber],
        fontsize=32)
    stepplotax.plot(x, out.init_fit, 'b-', label='init fit')
    stepplotax.plot(x, out.best_fit, 'r-', label='best fit')
    stepplotax.text(0, 0.71, "Ion wind = %2.2f" % out.params['windspeed'], transform=stepplotax.transAxes)
    stepplotax.text(0, .74, "IBS-derived SC Potential = %2.2f" % out.params['scp'], transform=stepplotax.transAxes)
    stepplotax.text(0, .77, "LP-derived SC Potential = %2.2f" % lpvalue, transform=stepplotax.transAxes)
    stepplotax.text(0, .8, "Temp = %2.2f" % out.params['temp'], transform=stepplotax.transAxes)
    for mass in ibs_masses:
        stepplotax.plot(x, comps["mass" + str(mass) + '_'], '--', label=str(mass) + " amu/q")
    stepplotax.legend(loc='best')


def ELS_fluxfitting(elsdata, time, seconds, anode, lpvalue=-1.3):
    for counter, i in enumerate(elsdata['times_utc_strings']):
        if i >= time:
            slicenumber = counter
            break

    temputc = str(titan_flybydates[elsdata['flyby']][0]) + '-' + str(titan_flybydates[elsdata['flyby']][1]) + '-' + str(
        titan_flybydates[elsdata['flyby']][2]) + 'T' + elsdata['times_utc_strings'][slicenumber]
    tempphase = cassini_phase(temputc)
    tempcassini_speed = np.sqrt((tempphase[3]) ** 2 + (tempphase[4]) ** 2 + (tempphase[5]) ** 2) * 1e3

    flowspeed = -150
    temperature = 150

    DEF = elsdata['def'][:, anode - 1, slicenumber]

    stepplotfig, stepplotax = plt.subplots()
    stepplotax.step(elscalib['polyearray'][:-1], DEF, where='post', label=elsdata['flyby'], color='k')
    stepplotax.errorbar(elscalib['earray'], DEF, yerr=[np.sqrt(i) for i in DEF], color='k', fmt='none')
    stepplotax.set_xlim(0, 20)
    stepplotax.set_ylim(bottom=1e6)
    stepplotax.set_yscale("log")
    stepplotax.set_ylabel("DEF [$m^{-2} s^{1} str^{-1} eV^{-1}$]", fontsize=20)
    stepplotax.set_xlabel("Energy (Pre-correction) [eV/q]", fontsize=20)
    stepplotax.tick_params(axis='both', which='major', labelsize=15)
    stepplotax.grid(b=True, which='major', color='k', linestyle='-', alpha=0.5)
    stepplotax.grid(b=True, which='minor', color='k', linestyle='--', alpha=0.25)
    stepplotax.minorticks_on()
    stepplotax.set_title(
        "Histogram of " + elsdata['flyby'].upper() + " els data from " + elsdata['times_utc_strings'][slicenumber],
        fontsize=32)
    # stepplotax.plot(elscalib['earray'],smoothedcounts_full,color='k')

    masses = [26, 50, 74, 117]
    x = elscalib['earray']
    out = total_fluxgaussian(x, DEF, masses, tempcassini_speed, flowspeed, lpvalue, temperature)

    stepplotax.plot(x, out.best_fit, 'r-', label='best fit')
    stepplotax.text(0, 0, "Ion wind = %2.2f" % out.params['flowspeed'], transform=stepplotax.transAxes)
    stepplotax.text(0, .05, "SC Potential = %2.2f" % out.params['scp'], transform=stepplotax.transAxes)
    stepplotax.text(0, .10, "Temp = %2.2f" % out.params['temp'], transform=stepplotax.transAxes)

    comps = out.eval_components(x=x)
    for mass in masses:
        stepplotax.plot(x, comps["mass" + str(mass) + '_'], '--', label=str(mass) + " amu/q")

    stepplotax.legend(loc='best')


windsdf = pd.read_csv("crosswinds_full.csv", index_col=0, parse_dates=True)
windsdf['Positive Peak Time'] = pd.to_datetime(windsdf['Positive Peak Time'])

# TO DO add LP potentials
# usedflybys = ['t16']
# for flyby in usedflybys:
#     els_ionwindspeeds, ibs_ionwindspeeds, ibs_residuals, ibs_scps = [], [], [], []
#     tempdf = windsdf[windsdf['Flyby'] == flyby.lower()]
#     elsdata = readsav("data/els/elsres_" + filedates[flyby] + ".dat")
#     generate_mass_bins(elsdata, flyby, "els")
#     ibsdata = readsav("data/ibs/ibsres_" + filedates[flyby] + ".dat")
#     generate_aligned_ibsdata(ibsdata, elsdata, flyby)
#     for i in tempdf['Positive Peak Time']:
#         print(i)
#         ibs_ionwindspeed, ibs_residual, ibs_scp = ibs_alongtrack_velocity(ibsdata, i)
#         ibs_ionwindspeeds.append(ibs_ionwindspeed)
#         ibs_residuals.append(ibs_residual)
#         ibs_scps.append(ibs_scp)
#
# testoutputdf = pd.DataFrame()
# testoutputdf['Bulk Time'] = tempdf['Bulk Time']
# testoutputdf['IBS Alongtrack velocity'] = ibs_ionwindspeeds
# testoutputdf['IBS residuals'] = ibs_residuals
# testoutputdf['IBS spacecraft potentials'] = ibs_scps
# testoutputdf.to_csv("testalongtrackvelocity.csv")
# #
# fig5, ax5 = plt.subplots()
# ax5.plot(tempdf['Positive Peak Time'], ibs_ionwindspeeds, color='C0', label="Ion Wind Speeds")
# ax5_1 = ax5.twinx()
# ax5_1.plot(tempdf['Positive Peak Time'], ibs_scps, color='C1', label="S/C potential, IBS derived")
# fig5.legend()

# Single slice test

flyby = 't16'
elsdata = readsav("data/els/elsres_" + filedates[flyby] + ".dat")
generate_mass_bins(elsdata, flyby, "els")
ibsdata = readsav("data/ibs/ibsres_" + filedates[flyby] + ".dat")
generate_aligned_ibsdata(ibsdata, elsdata, flyby)
tempdf = windsdf[windsdf['Flyby'] == flyby.lower()]

slicenumber = 0
print(tempdf['Positive Peak Time'].iloc[slicenumber])
ibs_ionwindspeed = IBS_fluxfitting(ibsdata, tempdf['Positive Peak Time'].iloc[slicenumber],tempdf['Altitude'].iloc[slicenumber])

plt.show()
