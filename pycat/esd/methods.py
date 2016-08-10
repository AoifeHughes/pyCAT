# (C) Wegener Center for Climate and Global Change, University of Graz, 2015
#
# This file is part of pyCAT.
#
# pyCAT is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License version 3 as published by the
# Free Software Foundation.
#
# pyCAT is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with pyCAT. If not, see <http://www.gnu.org/licenses/>.

"""
this file provides the actual correction functions with the following singature:
obs_cube (Cube), mod_cube(Cube), sce_cubes(list of Cubes), *args, **kwargs
"""

import logging
import numpy as np

def _quantile_mapping(obs_cube, mod_cube, sce_cubes, *args, **kwargs):
    """
    Quantile Mapping
    ----------------

    apply quantile mapping to all scenario cubes using the distributions
    of obs_cube and mod_cube

    Args:

    * obs_cube (iris.cube.Cube):
        the observational data

    * mod_cube (iris.cube.Cube):
        the model data at the reference period

    * sce_cubes (iris.cube.CubeList):
        the scenario data that shall be corrected
    """
    from statsmodels.tools.tools import ECDF
    
    cell_iterator = np.nditer(obs_cube.data[0], flags=['multi_index'])
    while not cell_iterator.finished:
        index_list = list(cell_iterator.multi_index)
        cell_iterator.iternext()

        index_list.insert(0,0)
        index = tuple(index_list)
        if obs_cube.data.mask[index]:
            continue

        index_list[0] = slice(0,None,1)
        index = tuple(index_list)
        obs_data = obs_cube.data[index]
        mod_data = mod_cube.data[index]
        mod_ecdf = ECDF(mod_data)

        for sce_cube in sce_cubes:
            sce_data = sce_cube[index].data
            p = mod_ecdf(sce_data)*100
            corr = np.percentile(obs_data, p) - \
                   np.percentile(mod_data, p)
            sce_cube.data[index] += corr


def _relative_sdm(
        obs_cube, mod_cube, sce_cubes, *args, **kwargs):
    """
    apply relative scaled distribution mapping to all scenario cubes
    assuming a gamma distributed parameter (with lower limit zero)

    Args:

    * obs_cube (iris.cube.Cube):
        the observational data

    * mod_cube (iris.cube.Cube):
        the model data at the reference period

    * sce_cubes (iris.cube.CubeList):
        the scenario data that shall be corrected

    Kwargs:

    * lower_limit (float):
        assume values below lower_limit to be zero (default: 0.1)

    * cdf_threshold (float):
        limit of the cdf-values (default: .99999999)
    """
    from scipy.stats import gamma

    lower_limit = kwargs.get('lower_limit', 0.1)
    cdf_threshold = kwargs.get('cdf_threshold', .99999999)
    
    cell_iterator = np.nditer(obs_cube.data[0], flags=['multi_index'])
    while not cell_iterator.finished:
        index_list = list(cell_iterator.multi_index)
        cell_iterator.iternext()

        index_list.insert(0,0)
        index = tuple(index_list)

        # consider only cells with valid observational data
        if obs_cube.data.mask[index]:
            continue

        index_list[0] = slice(0,None,1)
        index = tuple(index_list)

        obs_data = obs_cube.data[index]
        mod_data = mod_cube.data[index]
        obs_raindays = obs_data[obs_data>=lower_limit]
        mod_raindays = mod_data[mod_data>=lower_limit]
        obs_frequency = 1.*obs_raindays.shape[0] / obs_data.shape[0]
        mod_frequency = 1.*mod_raindays.shape[0] / mod_data.shape[0]
        obs_gamma = gamma.fit(obs_raindays, floc=0)
        mod_gamma = gamma.fit(mod_raindays, floc=0)

        obs_cdf = gamma.cdf(np.sort(obs_raindays), *obs_gamma)
        mod_cdf = gamma.cdf(np.sort(mod_raindays), *mod_gamma)
        obs_cdf[obs_cdf>cdf_threshold] = cdf_threshold
        mod_cdf[mod_cdf>cdf_threshold] = cdf_threshold
        
        for sce_cube in sce_cubes:
            sce_data = sce_cube[index].data
            sce_raindays = sce_data[sce_data>=lower_limit]
            sce_frequency = 1.*sce_raindays.shape[0] / sce_data.shape[0]
            sce_argsort = np.argsort(sce_data)
            sce_gamma = gamma.fit(sce_raindays, floc=0)

            expected_sce_raindays = min(
                np.round(len(sce_data) * obs_frequency * sce_frequency / mod_frequency),
                len(sce_data))

            sce_cdf = gamma.cdf(np.sort(sce_raindays), *sce_gamma)
            sce_cdf[sce_cdf>cdf_threshold] = cdf_threshold

            # interpolate cdf-values for obs and mod to the length of the scenario
            obs_cdf_intpol = np.interp(
                np.linspace(1, len(obs_raindays), len(sce_raindays)),
                np.linspace(1, len(obs_raindays), len(obs_raindays)),
                obs_cdf
            )
            mod_cdf_intpol = np.interp(
                np.linspace(1, len(mod_raindays), len(sce_raindays)),
                np.linspace(1, len(mod_raindays), len(mod_raindays)),
                mod_cdf
            )

            # adapt the observation cdfs
            obs_inverse = 1./(1-obs_cdf_intpol)
            mod_inverse = 1./(1-mod_cdf_intpol)
            sce_inverse = 1./(1-sce_cdf)
            adapted_cdf = 1-1./(obs_inverse*sce_inverse/mod_inverse)
            adapted_cdf[adapted_cdf<0.] = 0.

            # correct by adapted observation cdf-values
            xvals = gamma.ppf(np.sort(adapted_cdf), *obs_gamma) *\
                    gamma.ppf(sce_cdf, *sce_gamma) /\
                    gamma.ppf(sce_cdf, *mod_gamma)

            # interpolate to the expected length of future raindays
            correction = np.zeros(len(sce_data))
            if len(sce_raindays) > expected_sce_raindays:
                xvals = np.interp(
                    np.linspace(1, len(sce_raindays), expected_sce_raindays),
                    np.linspace(1, len(sce_raindays), len(sce_raindays)),
                    xvals
                )
            else:
                xvals = np.hstack((np.zeros(expected_sce_raindays-len(sce_raindays)), xvals))
                
                
            correction[sce_argsort[-expected_sce_raindays:]] = xvals
            sce_cube.data[index] = correction


def _absolute_sdm(
        obs_cube, mod_cube, sce_cubes, *args, **kwargs):
    """
    apply absolute scaled distribution mapping to all scenario cubes
    assuming a normal distributed parameter

    Args:

    * obs_cube (iris.cube.Cube):
        the observational data

    * mod_cube (iris.cube.Cube):
        the model data at the reference period

    * sce_cubes (iris.cube.CubeList):
        the scenario data that shall be corrected
    """
    from scipy.stats import norm
    from scipy.signal import detrend

    cdf_threshold = kwargs.get('cdf_threshold', .99999)

    cell_iterator = np.nditer(obs_cube.data[0], flags=['multi_index'])
    while not cell_iterator.finished:
        index_list = list(cell_iterator.multi_index)
        cell_iterator.iternext()

        index_list.insert(0,0)
        index = tuple(index_list)
        if obs_cube.data.mask[index]:
            continue

        index_list[0] = slice(0,None,1)
        index = tuple(index_list)

        # consider only cells with valid observational data
        obs_data = obs_cube.data[index]
        mod_data = mod_cube.data[index]

        obs_len = len(obs_data)
        mod_len = len(mod_data)
        
        obs_mean = obs_data.mean()
        mod_mean = mod_data.mean()

        # detrend the data
        obs_detrended = detrend(obs_data)
        mod_detrended = detrend(mod_data)

        obs_norm = norm.fit(obs_detrended)
        mod_norm = norm.fit(mod_detrended)

        obs_cdf = norm.cdf(np.sort(obs_detrended), *obs_norm)
        mod_cdf = norm.cdf(np.sort(mod_detrended), *mod_norm)
        obs_cdf = np.maximum(np.minimum(obs_cdf, cdf_threshold), 1-cdf_threshold)
        mod_cdf = np.maximum(np.minimum(mod_cdf, cdf_threshold), 1-cdf_threshold)
        
        for sce_cube in sce_cubes:
            sce_data = sce_cube[index].data

            sce_len = len(sce_data)
            sce_mean = sce_data.mean()
            
            sce_detrended = detrend(sce_data)
            sce_diff = sce_data - sce_detrended
            sce_argsort = np.argsort(sce_detrended)

            sce_norm = norm.fit(sce_detrended)
            sce_cdf = norm.cdf(np.sort(sce_detrended), *sce_norm)
            sce_cdf = np.maximum(np.minimum(sce_cdf, cdf_threshold), 1-cdf_threshold)

            # interpolate cdf-values for obs and mod to the length of the scenario
            obs_cdf_intpol = np.interp(
                np.linspace(1, obs_len, sce_len),
                np.linspace(1, obs_len, obs_len),
                obs_cdf
            )
            mod_cdf_intpol = np.interp(
                np.linspace(1, mod_len, sce_len),
                np.linspace(1, mod_len, mod_len),
                mod_cdf
            )

            # adapt the observation cdfs
            # split the tails of the cdfs around the center
            obs_cdf_shift = obs_cdf_intpol - .5
            mod_cdf_shift = mod_cdf_intpol - .5
            sce_cdf_shift = sce_cdf - .5
            obs_inverse = 1./(.5 - np.abs(obs_cdf_shift))
            mod_inverse = 1./(.5 - np.abs(mod_cdf_shift))
            sce_inverse = 1./(.5 - np.abs(sce_cdf_shift))
            adapted_cdf = np.sign(obs_cdf_shift) * (1.-1./(obs_inverse*sce_inverse/mod_inverse))
            adapted_cdf[adapted_cdf<0] += 1.
            adapted_cdf = np.maximum(np.minimum(adapted_cdf, cdf_threshold), 1-cdf_threshold)
            
            xvals = norm.ppf(np.sort(adapted_cdf), *obs_norm) +\
                    norm.ppf(sce_cdf, *sce_norm) - norm.ppf(sce_cdf, *mod_norm)
            xvals -= xvals.mean()
            xvals += obs_mean + (sce_mean - mod_mean)
            
            correction = np.zeros(sce_len)
            correction[sce_argsort] = xvals
            correction += sce_diff - sce_mean
            sce_cube.data[index] = correction

def _scaled_distribution_mapping(
        obs_cube, mod_cube, sce_cubes, *args, **kwargs):
    """
    apply scaled distribution mapping to all scenario cubes

    the method work differently for different meteorological parameters

    air_temperature: absolute sdm with normal distribution
    precipitation_amount: relative sdm with gamma distribution
    
    Args:

    * obs_cube (iris.cube.Cube):
        the observational data

    * mod_cube (iris.cube.Cube):
        the model data at the reference period

    * sce_cubes (iris.cube.CubeList):
        the scenario data that shall be corrected
    """
    implemented_parameters = {
        'air_temperature': _absolute_sdm,
        'precipitation_amount': _relative_sdm,
        'surface_downwelling_shortwave_flux_in_air': _relative_sdm,
    }
    try:
        implemented_parameters[obs_cube.standard_name](
            obs_cube, mod_cube, sce_cubes, *args, **kwargs)
    except:
        logging.error('SDM not implemented for {}'.format(obs_cube.standard_name))





def _scaled_distribution_mapping_gamma(
        obs_cube, mod_cube, sce_cubes, *args, **kwargs):
    """
    apply relative scaled distribution mapping to all scenario cubes
    this is for precipitation only! dry days are not accounted for
    when building the gamma-distribution

    Args:

    * obs_cube (iris.cube.Cube):
        the observational data

    * mod_cube (iris.cube.Cube):
        the model data at the reference period

    * sce_cubes (iris.cube.CubeList):
        the scenario data that shall be corrected

    Kwargs:

    * lower_limit (float):
        assume values below lower_limit to be zero

    """
    from scipy.stats import gamma

    lower_limit = kwargs.get('lower_limit', 0.)
    cell_iterator = np.nditer(obs_cube.data[0], flags=['multi_index'])
    while not cell_iterator.finished:
        index_list = list(cell_iterator.multi_index)
        cell_iterator.iternext()

        index_list.insert(0,0)
        index = tuple(index_list)

        # consider only cells with valid observational data
        if obs_cube.data.mask[index]:
            continue

        index_list[0] = slice(0,None,1)
        index = tuple(index_list)

        obs_data = obs_cube.data[index]
        mod_data = mod_cube.data[index]
        obs_raindays = obs_data[obs_data>=lower_limit]
        mod_raindays = mod_data[mod_data>=lower_limit]
        obs_frequency = 1.*obs_raindays.shape[0] / obs_data.shape[0]
        mod_frequency = 1.*mod_raindays.shape[0] / mod_data.shape[0]
        obs_gamma = gamma.fit(obs_raindays, floc=0)
        mod_gamma = gamma.fit(mod_raindays, floc=0)

        for sce_cube in sce_cubes:
            sce_data = sce_cube[index].daata
            sce_raindays = sce_data[sce_data>=lower_limit]
            sce_frequency = 1.*sce_raindays.shape[0] / sce_data.shape[0]
            sce_argsort = np.argsort(sce_data)
            sce_gamma = gamma.fit(sce_raindays, floc=0)

            expected_sce_raindays = min(
                np.round(len(sce_data) * obs_frequency * sce_frequency / mod_frequency),
                len(sce_data))

            pvals = gamma.cdf(np.sort(sce_raindays), *sce_gamma)
            xvals = gamma.ppf(pvals, *obs_gamma) * gamma.ppf(pvals, *sce_gamma) /\
                    gamma.ppf(pvals, *mod_gamma)
            correction = np.zeros(len(sce_data))
            if len(sce_raindays) > expected_sce_raindays:
                xvals = np.interp(
                    np.linspace(1, len(sce_raindays), expected_sce_raindays),
                    np.linspace(1, len(sce_raindays), len(sce_raindays)),
                    xvals
                )
            else:
                xvals = np.hstack((np.zeros(expected_sce_raindays-len(sce_raindays)), xvals))

            correction[sce_argsort[-expected_sce_raindays:]] = xvals

            sce_cube.data[index] = correction

def _scaled_distribution_mapping_norm(obs_cube, mod_cube, sce_cubes, *args, **kwargs):
    """
    apply relative scaled distribution mapping to all scenario cubes
    this is for precipitation only! dry days are not accounted for
    when building the gamma-distribution

    Args:

    * obs_cube (iris.cube.Cube):
        the observational data

    * mod_cube (iris.cube.Cube):
        the model data at the reference period

    * sce_cubes (iris.cube.CubeList):
        the scenario data that shall be corrected
    """
    from scipy.stats import norm
    from scipy.signal import detrend

    cdf_threshold = kwargs.get('cdf_threshold', .99999)

    cell_iterator = np.nditer(obs_cube.data[0], flags=['multi_index'])
    while not cell_iterator.finished:
        index_list = list(cell_iterator.multi_index)
        cell_iterator.iternext()

        index_list.insert(0,0)
        index = tuple(index_list)
        if obs_cube.data.mask[index]:
            continue

        index_list[0] = slice(0,None,1)
        index = tuple(index_list)

        # consider only cells with valid observational data
        obs_data = obs_cube.data[index]
        mod_data = mod_cube.data[index]

        obs_len = len(obs_data)
        mod_len = len(mod_data)
        
        obs_mean = obs_data.mean()
        mod_mean = mod_data.mean()

        # detrend the data
        obs_detrended = detrend(obs_data)
        mod_detrended = detrend(mod_data)

        # # skewness
        # obs_skew_sign = np.sign(skew(obs_detrended))
        # mod_skew_sign = np.sign(skew(mod_detrended))

        # # cdfs
        # obs_gamma = gamma.fit(obs_skew_sign * obs_data)
        # obs_cdf = np.sort(
        #     obs_skew_sign * (
        #         gamma.cdf(obs_skew_sign * obs_data, obs_gamma) + (obs_skew_sign-1)/2))
        # mod_gamma = gamma.fit(mod_skew_sign * mod_data)
        # mod_cdf = np.sort(
        #     mod_skew_sign * (
        #         gamma.cdf(mod_skew_sign * mod_data, mod_gamma) + (mod_skew_sign-1)/2))

        obs_norm = norm.fit(obs_detrended)
        mod_norm = norm.fit(mod_detrended)
        
        for sce_cube in sce_cubes:
            sce_data = sce_cube[index].data

            sce_len = len(sce_data)
            sce_mean = sce_data.mean()
            
            sce_detrended = detrend(sce_data)
            sce_diff = sce_data - sce_detrended
            sce_argsort = np.argsort(sce_detrended)

            sce_norm = norm.fit(sce_detrended)
            sce_cdf = norm.cdf(np.sort(sce_detrended), *sce_norm)
            sce_cdf = np.maximum(np.minimum(sce_cdf, cdf_threshold), 1-cdf_threshold)
            
            adapted_cdf = sce_cdf
            
            xvals = norm.ppf(adapted_cdf, *obs_norm) +\
                    norm.ppf(sce_cdf, *sce_norm) - norm.ppf(sce_cdf, *mod_norm) +\
                    obs_mean + sce_mean - mod_mean
            
            correction = np.zeros(sce_len)
            correction[sce_argsort] = xvals
            correction += sce_diff - sce_mean
            sce_cube.data[index] = correction
