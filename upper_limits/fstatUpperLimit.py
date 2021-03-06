#!/usr/bin/env python

# compute frequentist upper limits with the Fp statistic

from __future__ import division
import numpy as np
from scipy.optimize import brentq
import libstempo as t2
import PALutils
import PALLikelihoods
import PALpulsarInit
import h5py as h5
import argparse
import os, glob

parser = argparse.ArgumentParser(description = 'Simulate Fake Data (Under Construction)')

# options
parser.add_argument('--h5File', dest='h5file', action='store', type=str, required=True,
                   help='Full path to hdf5 file containing PTA data')
parser.add_argument('--freq', dest='freq', action='store', type=float, required=True,
                   help='Frequency at which to compute upper limit')
parser.add_argument('--nreal', dest='nreal', action='store', type=int, required=1000,
                   help='Number of realizations to use for each amplitude (default = 1000)')

# parse arguments
args = parser.parse_args()

##### PREPARE DATA STRUCTURES #####

# import hdf5 file
pfile = h5.File(args.h5file)

# define the pulsargroup
pulsargroup = pfile['Data']['Pulsars']

# fill in pulsar class
psr = [PALpulsarInit.pulsar(pulsargroup[key],addNoise=True) for key in pulsargroup]

# number of pulsars
npsr = len(psr)

# make sure all pulsar have same reference time
tt=[] 
for p in psr:
    tt.append(np.min(p.toas))

# find reference time
tref = np.min(tt)

# now scale pulsar time
for p in psr:
    p.toas -= tref

# read in tim and par files
parFile = [pulsargroup[key]['parFile'].value for key in pulsargroup]
timFile = [pulsargroup[key]['timFile'].value for key in pulsargroup]

# close hdf5 file
pfile.close()

# check to make sure same number of tim and par files
if len(parFile) != len(timFile):
    raise IOError, "Need same number of par and tim files!"

# check to make sure same number of tim/par files as was in hdf5 file
if len(parFile) != npsr:
    raise IOError, "Different number of pulsars in par directory and hdf5 file!"

# run tempo2
pp = [t2.tempopulsar(parFile[ii], timFile[ii]) for ii in range(npsr)]

# finally check to make sure that they are the same pulsars
for ct,p in enumerate(psr):
    if p.name not in  [ps.name for ps in pp]:
        raise IOError, "PSR {0} not found in hd5f file!".format(p.name)

# make sure pulsar names are in correct order
# TODO: is this a very round about way to do this?
index = []
for ct,p in enumerate(pp):
    
    if p.name == psr[ct].name:
        index.append(ct)
    else:
        for ii in range(npsr):
            if pp[ii].name == psr[ct].name:
                index.append(ii)

pp = [pp[ii] for ii in index]

M = [PALutils.createQSDdesignmatrix(p.toas) for p in psr]

RQ = [PALutils.createRmatrix(M[ct], p.err) for ct, p in enumerate(psr)]

# construct noise matrix for new noise realizations
print 'Constructing noise cholesky decompositions'
L = []
for ct, p in enumerate(psr):

    Amp = p.Amp
    gam = p.gam
    efac = p.efac
    equad = p.equad
    cequad = p.cequad
        
    avetoas, U = PALutils.exploderMatrix(p.toas)
    Tspan = p.toas.max()-p.toas.min()
    F, f = PALutils.createfourierdesignmatrix(p.toas, 10, freq=True, Tspan=Tspan)
            
    f1yr = 1/3.16e7
    rho = (Amp**2/12/np.pi**2 * f1yr**(gam-3) * f**(-gam)/Tspan)
    
    tmp = np.zeros(20)
    tmp[0::2] = rho
    tmp[1::2] = rho
    
    phi = np.diag(tmp)
    
    white = PALutils.createWhiteNoiseCovarianceMatrix(p.err, efac**2, equad)
    
    cequad_mat = cequad**2 * np.dot(U,U.T)
    
    red = np.dot(F, np.dot(phi, F.T))
    
    cov = white + red + cequad_mat

    L.append(np.linalg.cholesky(cov))

#############################################################################################

#### DEFINE UPPER LIMIT FUNCTION #####

def upperLimitFunc(h):
    """
    Compute the value of the fstat for a range of parameters, with fixed
    amplitude over many realizations.

    @param h: value of the strain amplitude to keep constant
    @param fstat_ref: value of fstat for real data set
    @param freq: GW frequency
    @param nreal: number of realizations

    """
    
    Tmaxyr = np.array([(p.toas.max() - p.toas.min())/3.16e7 for p in psr]).max()
    count = 0
    for ii in range(nreal):

        # draw parameter values
        gwtheta = np.arccos(np.random.uniform(-1, 1))
        gwphi = np.random.uniform(0, 2*np.pi)
        gwphase = np.random.uniform(0, 2*np.pi)
        gwinc = np.arccos(np.random.uniform(-1, 1))
        gwpsi = np.random.uniform(-np.pi/4, np.pi/4)

        # check to make sure source has not coalesced during observation time
        gwmc = 10**np.random.uniform(7, 10)
        tcoal = 2e6 * (gwmc/1e8)**(-5/3) * (freq/1e-8)**(-8/3)
        if tcoal < Tmaxyr:
            gwmc = 1e5

        # determine distance in order to keep strain fixed
        gwdist = 4 * np.sqrt(2/5) * (gwmc*4.9e-6)**(5/3) * (np.pi*freq)**(2/3) / h

        # convert back to Mpc
        gwdist /= 1.0267e14

        # create residuals and refit for all pulsars
        for ct,p in enumerate(psr):
            inducedRes = PALutils.createResiduals(p, gwtheta, gwphi, gwmc, gwdist, \
                            freq, gwphase, gwpsi, gwinc)
 
            # create simulated data set
            noise = np.dot(L[ct], np.random.randn(L[ct].shape[0]))
            pp[ct].stoas[:] -= pp[ct].residuals()/86400
            pp[ct].stoas[:] += np.longdouble(np.dot(RQ[ct], noise)/86400)
            pp[ct].stoas[:] += np.longdouble(np.dot(RQ[ct], inducedRes)/86400)

            # refit
            pp[ct].fit(iters=3)

            # replace residuals in pulsar object
            p.res = pp[ct].residuals()

            print p.name, p.rms()*1e6

        # compute f-statistic
        fpstat = PALLikelihoods.fpStat(psr, freq)

        # check to see if larger than in real data
        if fpstat > fstat_ref:
            count += 1

    # now get detection probability
    detProb = count/nreal

    print h, detProb

    return detProb - 0.95


#############################################################################################

hhigh = 1e-13
hlow = 1e-15
xtol = 1e-16
nreal = args.nreal
freq = args.freq

# get reference f-statistic
fstat_ref = PALLikelihoods.fpStat(psr, freq)

# perfrom upper limit calculation
inRange = False
while inRange == False:

    try:    # try brentq method
        h_up = brentq(upperLimitFunc, hlow, hhigh, xtol=xtol)
        inRange = True
    except ValueError:      # bounds not in range
        if hhigh < 1e-11:   # don't go too high
            hhigh *= 2      # double high strain
        else:
            h_up = hhigh    # if too high, just set to upper bound
            inRange = True

