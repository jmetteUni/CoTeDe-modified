# -*- coding: utf-8 -*-

"""


Target: Initially two main functionalities

- Quality control flagging
    - Input:
        - Lista de arquivo | diretório
        - Q.C. config (can be a file)
        - Features (just varnames)
    - Pandas Collection:
        - Load all files and return flags plus features (aux)
    - Split groups: fit, test, eval
    - Calibrate:
        - Fit PDF parameters for each feature using fit group
        - Define the optimal thresholds comparing with flags from Q.C. config
            - There is the possibility to human interaction to overwrite the flags from the auto Q.C.
        - Estimate the error
        - The threshold between good or bad flags were defined on the second step, but now consider the whole dataset. The highest probability of any bad value is the thresholds between 1 and 2. In other words, all data with higher probability that this threshold where good. Between this threshold and the optimal previously defined there were bad data, but it was mostly good ones, hence flag 2 which means probably good. With the same concept define the threshold between flags 3 and 4.

    With the coeficients determined, an independent procedure to flag all data. Independent because the anomaly detection flagging itself can simply be loaded by previsouly defined coeficients, hence start already from this point.
    - Fit features, on full DB, there is no split data
    - With the parameters on the previous step estimate the probability of each measurement
    - Create a list sorted by:
        - Produtorium of all probabilities or,
        - Min(P(x_i)), the lowest probability for each measurement
    - The output would be a list to feed the Human Q.C. system
"""

import numpy as np
from numpy import ma
# from scipy.stats import norm, rayleigh, expon, halfnorm, exponpow, exponweib
from scipy.stats import exponweib
# from scipy.stats import kstest

from cotede.utils import ProfilesQCPandasCollection
from cotede.misc import combined_flag
from cotede.humanqc import HumanQC


def fit_tests(features, ind=True, q=0.90, verbose=False):
    """

        Input:
          features: a dictionary like with the numerical results from the
              QC tests. For example, the gradient test values, not the
              flags, but the floats itself, like
              {'gradient': ma.array([.23, .12, .08]), 'spike': ...}
          ind: The features values positions to be considered in the fit.
              It's usefull to eliminate out of range data, or to
              restrict to a subset of the data, like in the calibration
              procedure.
          q: The lowest percentile to be considered. For example, .90
              means that only the top 10% data (i.e. percentiles higher
              than .90) are considered in the fitting.
    """
    output = {}
    for test in features:
        samp = features[test][ind & np.isfinite(features[test])]
        ind_top = samp > samp.quantile(q)
        if ind_top.any():
            param = exponweib.fit(samp[ind_top])
            output[test] = {'param': param,
                    'qlimit': samp.quantile(q)}

        if verbose is True:
            import pylab
            x = np.linspace(samp[ind_top].min(), samp[ind_top].max(), 100)
            pdf_fitted = exponweib.pdf(x, *param[:-2], loc=param[-2], scale=param[-1])
            pylab.plot(x, pdf_fitted, 'b-')
            pylab.hist(ma.array(samp[ind_top]), 100, normed=1, alpha=.3)
            pylab.title(test)
            pylab.show()

    return output


def estimate_anomaly(features, params, method='produtorium'):
    """ Estimate probability from PDF defined by params

        The output is the natural logarithm of the estimated probability.

        params are the parameters that define the PDF for each feature
          in features. This function estimate the combined probability of
          each row in features as the produtorium between the probabilities
          of the different features on the same row.

        ATENTION!! I should think more about what would I like from this
          function. What should happens in case of a masked feature? And
          if all features for one measurement are masked? Right now it
          simply don't add for the estimate, so that all features masked
          would lead to an expectation of 100% it's good.
    """
    assert hasattr(params, 'keys')
    assert hasattr(features, 'keys')

    prob = ma.zeros(len(features[features.keys()[0]]))

    for t in params.keys():
        param = params[t]['param']
        ind = ~ma.getmaskarray(features[t])

        tmp = exponweib.sf(np.asanyarray(features[t])[ind],
                *param[:-2], loc=param[-2], scale=param[-1])
        # Arbitrary solution. No value can have a probability of 0.
        tmp[tmp == 0] = 1e-15
        p = ma.log(tmp)

        if method == 'produtorium':
            prob[ind] = prob[ind] + p
        elif method == 'min':
            prob[ind] = min(prob[ind], p)
        else:
            return

    return prob


def estimate_p_optimal(prob, binflag, verbose=False):
    """ ATENTION: I'm not happy with this. Improve it

        Maybe use flag as input, and optimize to give 3 thresholds
    """
    assert prob.shape == binflag.shape
    assert binflag.dtype == 'bool'

    err = []
    p_limit = prob[np.nonzero(binflag)].min() - 0.1
    P = -np.arange(0, -p_limit, 0.1)
    N = P.size
    err = np.empty(N)
    false_negative = np.empty(N)
    false_positive = np.empty(N)
    for i, p in enumerate(P):
        # The nonzero is necessary in case binflag is a masked array.
        false_negative[i] = np.nonzero(prob[np.nonzero(binflag)] < p)[0].size
        false_positive[i] = np.nonzero(prob[np.nonzero(~binflag)] > p)[0].size
        err[i] = false_negative[i] + false_positive[i]

    if verbose is True:
        import pylab
        pylab.plot(P, err , 'b'); pylab.show()

    return P[err.argmin()], float(err.min())/prob.size#, {'P': P, 'err': err}


def calibrate4flags(flags, features, q=0.90, verbose=False):
    """ Adjust coeficients for Anomaly Detection to best reproduce given flags

        Inputs:
            flag_ref: Reference index. What the Anomaly Detection will try
                   to reproduce. Uses the True and Falses from flag_ref
                   to partition the data to be used to fit, to adjust
                   and to estimate the error.
            qctests: The tests used by the Anomaly Detection. One curve will
                   be fit for each test.
            aux: The auxiliary tests results from the ProfileQCCollection. It
                   is expected that the qctests are present in aux.
            q: The top q extreme tests results to be used on Anom. Detect.
                 For example q=0 will use all the data, while q=0.9 (default)
                 will use the percentile of 0.9, i.e. the top 10% values.

            Output: Returns a dictionary with
                err:
                err_ratio:
                false_negative:
                false_positive:
                p_optimal:
                params:

            Use the functions:
                split_data_groups()
                fit_tests()
                estimate_anomaly()
                estimate_p_optimal()

    """
    assert hasattr(flags, 'keys')

    indices = split_data_groups(combined_flag(flags))
    params = fit_tests(features[indices['fit']], q=q)
    prob = estimate_anomaly(features, params)

    if verbose is True:
        pylab.hist(prob)
        pylab.show()

    binflags = flags2bin(combined_flag(flags))
    p_optimal, test_err = estimate_p_optimal(prob[indices['test']],
            binflags[indices['test']])

    false_negative = (prob < p_optimal) & binflags
    false_positive = (prob > p_optimal) & ~binflags

    mistake = false_positive | false_negative

    # I can extract only .data, since split_data_groups already eliminated
    #   all non valid positions.
    #err = np.nonzero(false_negative)[0].size + \
    #        np.nonzero(false_positive)[0].size
    n_err = float(np.nonzero(mistake[indices['err']])[0].shape[0])
    #err_ratio = float(err)/prob[indices['ind_err']].size
    err_ratio = n_err/indices['err'].astype('i').sum()
    #false_negative = (prob < p_optimal) & \
    #    (flag_ref.data is True) & (ma.getmaskarray(flag_ref) is False)
    #false_positive = (prob > p_optimal) & \
    #    (flag_ref.data is False) & (ma.getmaskarray(flag_ref) is False)

    output = {'false_negative': false_negative,
            'false_positive': false_positive,
            'prob': prob,
            'p_optimal': p_optimal,
            'n_err': n_err,
            'err_ratio': err_ratio,
            'params': params}

    return output


def split_data_groups(flag, good_flags=[1,2], bad_flags=[3,4]):
    """ Splits randomly the indices into fit, test and error groups

        Return a dictionary with 3 indices set:
            - ind_fit with 60% of the good
            - ind_test with 20% of the good and 50% of the bad
            - ind_eval with 20% of the good and 50% of the bad
    """
    assert flag.dtype != 'bool'

    ind = ma.masked_all(len(flag), dtype='bool')
    for f in good_flags:
        ind[flag == f] = True
    for f in bad_flags:
        ind[flag == f] = False

    N = ind.size
    ind_base = np.zeros(N) == 1
    ind_valid = ~ma.getmaskarray(ind)

    # ==== Good data ==================
    ind_good = np.nonzero((ind == True)  & ind_valid)[0]
    N_good = ind_good.size
    perm = np.random.permutation(N_good)
    N_test = int(round(N_good*.2))
    ind_test = ind_base.copy()
    ind_test[ind_good[perm[:N_test]]] = True
    ind_err = ind_base.copy()
    ind_err[ind_good[perm[N_test:2*N_test]]] = True
    ind_fit = ind_base.copy()
    ind_fit[ind_good[perm[2*N_test:]]] = True

    # ==== Bad data ===================
    ind_bad = np.nonzero((ind == False) & ind_valid)[0]
    N_bad = ind_bad.size
    perm = np.random.permutation(N_bad)
    N_test = int(round(N_bad*.5))
    ind_test[ind_bad[perm[:N_test]]] = True
    ind_err[ind_bad[perm[N_test:]]] = True

    return {'fit': ind_fit, 'test': ind_test, 'err': ind_err}


def flags2bin(flags, good_flags=[1,2], bad_flags=[3,4]):
    """
    """

    if hasattr(flags, 'keys'):
        # The different flags must have same ammount of data.
        N = len(flags[flags.keys()[0]])
        for f in flags:
            assert len(flags[f]) == N

        flags = combined_flag(flags, reference_flags)
    else:
        N = len(flags)

    output = ma.masked_all(N, dtype='bool')
    for f in good_flags:
        output[flags == f] = True
    for f in bad_flags:
        output[flags == f] = False

    return output


def calibrate_anomaly_detection(datadir, varname, cfg=None):
    """ Calibrate coefficientes for Anomaly Detection

        Input:
            datadir: Directory with the data to be used on calibration
            varname: Variable to calibrate. For example: TEMP
            cfg: CoTeDe's QC configuration. Can be None for CoTeDe's default
                a name for one of the preset configuration files, or a dict

        Output:
            false_negative:
            false_positive:
            prob:
            p_optimal:
            err:
            err_ratio:
            params:

        Loads all the data in datadir, apply the Q.C. procedures according to
          cfg, and than calibrate params and p_optimal so that anomaly
          detection reproduces the combined flags from the Q.C.
    """
    import pandas as pd

    assert type(varname) is str, "varname must be a string"

    db = ProfilesQCPandasCollection(datadir, cfg=cfg, saveauxiliary=True)

    assert varname in db.keys()

    # # Remove the value out of the possible range.
    # ind_outofrange = np.nonzero(db.flags[varname]['global_range'] != 1)
    # binflag.mask[ind_outofrange] = True
    # hardlimit_flags = ['global_range']
    ind = db.flags[varname]['global_range'] == 1
    #aux = db.auxiliary[varname][ind]
    #features = aux.drop(['id','profileid'], axis=1)
    features = db.auxiliary[varname][ind]
    #flags = db.flags[varname][ind].drop(['id','profileid', 'density_inversion'], axis=1)
    #flags = db.flags[varname][ind].drop(['density_inversion'], axis=1)
    #flags = combined_flag(flags)
    #flags = combined_flag(db.flags[varname][ind])
    #binflags = flags2bin(flags)

    result = calibrate4flags(db.flags[varname][ind],
            db.auxiliary[varname][ind], q=0.90, verbose=False)

    #ind = ma.masked_all(len(flags), dtype='bool')
    #ind[(flags == 1) | (flags == 2)] = True
    #ind[(flags == 3) | (flags == 4)] = False
    #indices = split_data_groups(ind)
    #indices = split_data_groups(flags)


    #params = fit_tests(features[indices['fit']], q=.9)
    #prob = estimate_anomaly(features, params)

    #binflag = flags2binflag(db.flags[varname][ind], reference_flags)

    #p_optimal, test_err = estimate_p_optimal(prob[indices['test']],
    #        flags2bin(flags[indices['test']]))

    #false_negative = prob[indices['err'] & binflags] < p_optimal
    #false_positive = prob[indices['err'] & ~binflags] < p_optimal
    #err = np.nonzero(false_negative)[0].size + \
    #        np.nonzero(false_positive)[0].size
    #err_ratio = float(err)/prob[indices['err']].size

    #output = {'false_negative': false_negative,
    #        'false_positive': false_positive,
    #        'prob': prob,
    #        'p_optimal': p_optimal,
    #        'err': err,
    #        'err_ratio': err_ratio,
    #        'params': params}

    #result = adjust_anomaly_coefficients(binflag, qctests, aux)

    #return output
    return result


def human_calibrate_mistakes(datadir, varname, cfg=None, niter=5):
    """
    """
    import pandas as pd

    #qctests = ['gradient', 'step', 'tukey53H_norm', 'woa_relbias']
    #reference_flags = ['global_range', 'gradient_depthconditional',
    #        'spike_depthconditional', 'digit_roll_over']
    # hardlimit_flags = ['global_range']

    db = ProfilesQCPandasCollection(datadir, cfg=cfg, saveauxiliary=True)

    assert varname in db.keys()

    # Remove the value out of the possible range.
    ##data = db.data.loc[ind_valid, ['profileid', 'pressure', varname]]
    #ind = db.flags[varname]['global_range'] == 1
    #data = db.data.loc[ind]
    data = db.data
    #aux = db.auxiliary[varname].loc[ind]
    #features = aux.drop(['id','profileid'], axis=1)
    features = db.auxiliary[varname]
    #flags = db.flags[varname].loc[ind]
    #flags = flags.drop(['id','profileid', 'density_inversion'], axis=1)
    flags = db.flags[varname]
    binflags = flags2bin(combined_flag(flags))


    result = calibrate4flags(db.flags[varname],
            db.auxiliary[varname], q=0.90, verbose=False)

    indices = split_data_groups(combined_flag(flags))

    params = fit_tests(features[indices['fit']], q=.9)
    prob = estimate_anomaly(features, params)

    p_optimal, test_err = estimate_p_optimal(prob[indices['test']],
            flags2bin(combined_flag(flags)[indices['test']]))
    #false_negative = prob[indices['err'] & binflags] < p_optimal
    #false_positive = prob[indices['err'] & ~binflags] < p_optimal
    false_negative = (prob < p_optimal) & binflags
    false_positive = (prob > p_optimal) & ~binflags
    #mistakes = np.nonzero(false_positive | false_negative)[0]
    mistake = false_positive | false_negative

    n_err = float(np.nonzero(mistake[indices['err']])[0].shape[0])
    err_ratio = n_err/indices['err'].astype('i').sum()

    #profileslist = aux['profileid'].iloc[mistake].iloc[
    #        np.absolute(prob[mistake] - p_optimal).argsort()
    #        ].unique()

    #result = adjust_anomaly_coefficients(binflags, qctests=features.keys(), aux=features)
    #error_log = [{'err': result['err'], 'err_ratio': result['err_ratio'],
    #                 'p_optimal': result['p_optimal']}]
    error_log = [{'err': n_err, 'err_ratio': err_ratio,
                     'p_optimal': p_optimal}]

    # I don't like this approach. Improve this in the future.
    if 'human' not in flags:
        #flags['human'] = np.nan
        #flags['human'] = None
        flags['human'] = 0
        doubt = np.zeros(len(flags['human']), dtype='bool')

    #ind_humanqc = binflags.copy()
    for i in range(niter):
        import pdb; pdb.set_trace()
        # Profiles with any failure
        #mistakes = (result['false_positive'] | result['false_negative'])
        #profileids = db[varname]['profileid'].iloc[mistakes].unique()
        # Must be without humaneval
        #profileids = data['profileid'].iloc[mistake].iloc[
        #    np.absolute(prob[mistake] - p_optimal).argsort()
        #    ].unique()
        derr = np.absolute(prob[np.nonzero(mistake)] - p_optimal)
        #ind_toeval = mistake & (flags['human'] != 6)
        ind_toeval = np.nonzero(mistake & ~doubt)
        profileids = data['profileid'].iloc[ind_toeval].iloc[derr.argsort()
            ].unique()
        #if len(profileids) == 0:
        #    break
        # Only 5 profiles each time
        #for pid in np.random.permutation(profileids)[:5]:
        import pdb; pdb.set_trace()
        for pid in profileids[:3]:
            print("Profile: %s" % pid)
            ind_p = data.profileid == pid
            #print data.profilename[ind_p].iloc[0]
            #mistakes = (result['false_positive'][ind_p] |
            #        result['false_negative'][ind_p])
            mistake.mask[doubt] = True
            h = HumanQC().eval(
                    data[varname][ind_p],
                    data['PRES'][ind_p],
                    baseflag=binflags[np.array(ind_p)],
                    fails=mistake[np.array(ind_p)])  #, doubt = ind_doubt[ind])
            #ind_humanqc[np.nonzero(ind_p)[0][h == 'good']] = True
            flags.loc[np.nonzero(ind_p)[0][h == 'good'], 'human'] = 1
            #ind_humanqc[np.nonzero(ind_p)[0][h == 'bad']] = False
            flags.loc[np.nonzero(ind_p)[0][h == 'bad'], 'human'] = 4
            #flags.loc[np.nonzero(ind_p)[0][h == 'doubt'], 'human'] = 6
            doubt[np.nonzero(ind_p)[0][h == 'doubt']] = True
            #ind_humanqc.mask[np.nonzero(ind_p)[0][h == 'doubt']] = True

        #result = adjust_anomaly_coefficients(ind_humanqc, qctests, aux)
        binflags = flags2bin(combined_flag(flags))
        indices = split_data_groups(combined_flag(flags))
        params = fit_tests(features[indices['fit']], q=.9)
        prob = estimate_anomaly(features, params)
        p_optimal, test_err = estimate_p_optimal(prob[indices['test']],
                flags2bin(combined_flag(flags)[indices['test']]))
        false_negative = (prob < p_optimal) & binflags
        false_positive = (prob > p_optimal) & ~binflags
        mistake = false_positive | false_negative

        #n_err = float(np.nonzero(mistake[indices['err']])[0].shape[0])
        #err_ratio = n_err/indices['err'].astype('i').sum()

        #result = calibrate4flags(flags, features, q=0.90, verbose=False)


        error_log.append({'err': n_err,
            'err_ratio': err_ratio,
            'p_optimal': p_optimal})
        #error_log.append({'err': result['n_err'],
        #    'err_ratio': result['err_ratio'],
        #    'p_optimal': result['p_optimal']})

        print error_log[-2]
        print error_log[-1]

    return {'ind_humanqc': binflags, 'error_log': error_log,
            'p_optimal': p_optimal}
    #return {'ind_humanqc': binflags, 'error_log': error_log,
    #        'result': result}


def rank_files(datadir, varname, cfg=None):
    """
        Ordered list from datadir files of probably bad data


        The concept is for a recommendation system for Human Q.C.

            - Input: Lista de arquivo | diretório
            - Pandas Collection:
                - Load all files and return flags plus features (aux)
            - Fit features, on full DB, there is no split data
            - With the parameters on the previous step estimate the
                probability of each measurement
            - Create a list sorted by:
                - Produtorium of all probabilities or,
                - Min(P(x_i)), the lowest probability for each measurement
            - The output would be a list to feed the Human Q.C. system
    """
    import pandas as pd

    assert type(varname) is str

    db = ProfilesQCPandasCollection(datadir, cfg=cfg, saveauxiliary=True)

    # hardlimit_flags = ['global_range']
    ind = db.flags[varname]['global_range'] == 1
    aux = db.auxiliary[varname][ind]
    features = aux.drop(['id','profileid'], axis=1)

    params = fit_tests(features, q=.9)
    # Note that I'm already filtering to positions ind, i.e. valid
    #   global range limits. Global range is too obvious and should
    #   be left aside.
    prob = estimate_anomaly(aux, params)

    tmp = db.data.loc[ind, ['profilename']]
    tmp.loc[:, 'anomaly_detection'] = pd.Series(prob, index=tmp.index)
    grp = tmp.groupby('profilename')
    output = grp.min().sort('anomaly_detection').index.tolist()

    return output

    # profilename = np.asanyarray(db.data['profilename'])
    # output = []
    # for pid in np.unique(profilename):
    #     output.append([pid, min(prob[profilename == pid])])

    # return [x[0] for x in sorted(output, key=lambda x: x[1])]
