# -*- coding: utf-8 -*-
import warnings
import numpy as np
import scipy
from sklearn.decomposition import PCA, FastICA

from .microstates_prepare_data import _microstates_prepare_data
from .microstates_quality import microstates_gev, microstates_crossvalidation
from .microstates_classify import microstates_classify
from ..stats import cluster


def microstates_segment(eeg, n_microstates=4, train="gfp", method='marjin', gfp_method='l1', sampling_rate=None,
                        standardize_eeg=False, n_runs=10, max_iterations=1000, criterion='gev', seed=None, **kwargs):
    """Segment a continuous M/EEG signal into microstates using different clustering algorithms.

    Several runs of the clustering algorithm are performed, using different random initializations.
    The run that resulted in the best segmentation, as measured by global explained variance
    (GEV), is used.

    The microstates clustering is typically fitted on the EEG data at the global field power (GFP)
    peaks to maximize the signal to noise ratio and focus on moments of high global neuronal
    synchronization. It is assumed that the topography around a GFP peak remains stable and is at
    its highest signal-to-noise ratio at the GFP peak.

    Parameters
    ----------
    eeg : np.ndarray
        An array (channels, times) of M/EEG data or a Raw or Epochs object from MNE.
    n_microstates : int
        The number of unique microstates to find. Defaults to 4.
    train : Union[str, int, float]
        Method for selecting the timepoints how which to train the clustering algorithm. Can be
        'gfp' to use the peaks found in the Peaks in the global field power. Can be 'all', in which
        case it will select all the datapoints. It can also be a number or a ratio, in which case
        it will select the corresponding number of evenly spread data points. For instance,
        ``train=10`` will select 10 equally spaced datapoints, whereas ``train=0.5`` will select
        half the data. See ``microstates_peaks()``.
    method : str
        The algorithm for clustering. Can be one of 'kmeans', the modified k-means algorithm 'kmod',
        'pca' (Principal Component Analysis), 'ica' (Independent Component Analysis), or
        'aahc' (Atomize and Agglomerate Hierarchical Clustering) which is more computationally heavy.
    gfp_method : str
        The GFP extraction method, can be either 'l1' (default) or 'l2' to use the L1 or L2 norm.
        See ``nk.eeg_gfp()`` for more details.
    sampling_rate : int
        The sampling frequency of the signal (in Hz, i.e., samples/second).
    standardize_eeg : bool
        Standardized (z-score) the data across time prior to GFP extraction
        using ``nk.standardize()``.
    n_runs : int
        The number of random initializations to use for the k-means algorithm.
        The best fitting segmentation across all initializations is used.
        Defaults to 10.
    max_iterations : int
        The maximum number of iterations to perform in the k-means algorithm.
        Defaults to 1000.
    criterion : str
        Which criterion to use to choose the best run for modified k-means algorithm,
        can be 'gev' (default) which selects
        the best run based on the highest global explained variance, or 'cv' which selects the best run
        based on the lowest cross-validation criterion. See ``nk.microstates_gev()``
        and ``nk.microstates_crossvalidation()`` for more details respectively.
    seed : Union[int, numpy.random.RandomState]
        The seed or ``RandomState`` for the random number generator. Defaults
        to ``None``, in which case a different seed is chosen each time this
        function is called.

    Returns
    -------
    dict
        Contains information about the segmented microstates:
        - **Microstates**: The topographic maps of the found unique microstates which has a shape of
        n_channels x n_states
        - **Sequence**: For each sample, the index of the microstate to which the sample has been assigned.
        - **GEV**: The global explained variance of the microstates.
        - **GFP**: The global field power of the data.
        - **Cross-Validation Criterion**: The cross-validation value of the iteration.
        - **Explained Variance**: The explained variance of each cluster map generated by PCA.
        - **Total Explained Variance**: The total explained variance of the cluster maps generated by PCA.

    Examples
    ---------
    >>> import neurokit2 as nk
    >>>
    >>> eeg = nk.mne_data("filt-0-40_raw").filter(1, 35)
    >>> eeg = nk.eeg_rereference(eeg, 'average')

    >>> # Kmeans
    >>> out_kmeans = nk.microstates_segment(eeg, method='kmeans')
    >>> nk.microstates_plot(out_kmeans, gfp=out_kmod["GFP"][0:500]) #doctest: +ELLIPSIS
    <Figure ...>

    >>> # Modified kmeans
    >>> out_kmod = nk.microstates_segment(eeg, method='kmod')
    >>> nk.microstates_plot(out_kmod, gfp=out_kmod["GFP"][0:500]) #doctest: +ELLIPSIS
    <Figure ...>
    >>>
    >>> # PCA
    >>> out_pca = nk.microstates_segment(eeg, method='pca', standardize_eeg=True)
    >>> nk.microstates_plot(out_pca, gfp=out_pca["GFP"][0:500]) #doctest: +ELLIPSIS
    <Figure ...>
    >>>
    >>> # ICA
    >>> out_ica = nk.microstates_segment(eeg, method='ica', standardize_eeg=True)
    >>> nk.microstates_plot(out_ica, gfp=out_ica["GFP"][0:500]) #doctest: +ELLIPSIS
    <Figure ...>
    >>>
    >>> # AAHC
    >>> out_aahc = nk.microstates_segment(eeg, method='aahc')
    >>> nk.microstates_plot(out_aahc, gfp=out_aahc["GFP"][0:500]) #doctest: +ELLIPSIS
    <Figure ...>


    See Also
    --------
    eeg_gfp, microstates_peaks, microstates_gev, microstates_crossvalidation, microstates_classify

    References
    ----------
    - Pascual-Marqui, R. D., Michel, C. M., & Lehmann, D. (1995). Segmentation of brain
    electrical activity into microstates: model estimation and validation. IEEE Transactions
    on Biomedical Engineering.

    """
    # Sanitize input
    data, indices, gfp, info = _microstates_prepare_data(eeg,
                                                         train=train,
                                                         sampling_rate=sampling_rate,
                                                         standardize_eeg=standardize_eeg,
                                                         gfp_method=gfp_method,
                                                         **kwargs)

    # Normalizing constant (used later for GEV)
    gfp_sum_sq = np.sum(gfp**2)

    # Do several runs of the k-means algorithm, keep track of the best segmentation.
#    best_gev = 0
#    best_microstates = None
#    best_segmentation = None
    segmentation_list = []
    microstates_list = []
    cv_list = []
    gev_list = []

    # Random timepoints
    if not isinstance(seed, np.random.RandomState):
        seed = np.random.RandomState(seed)

    # Run choice of clustering algorithm
    if method in ["kmods", "kmod", "kmeans modified", "modified kmeans"]:
        for i in range(n_runs):
            init_times = seed.choice(len(indices), size=n_microstates, replace=False)
            segmentation, microstates, info = cluster(data[:, indices].T, method=method, init_times=init_times,
                                                      n_clusters=n_microstates, random_state=seed,
                                                      max_iterations=max_iterations, threshold=1e-6)
    elif method in ["ica", "independent component", "independent component analysis"]:
        segmentation, microstates, info = cluster(data[:, indices].T, method=method,
                                                  n_clusters=n_microstates, random_state=seed,
                                                  max_iterations=max_iterations)
    elif method in ["pca", "principal component analysis", "principal component"]:
        segmentation, microstates, info = cluster(data[:, indices].T, method=method,
                                                  n_clusters=n_microstates, random_state=seed)
    else:
        segmentation, microstates, info = cluster(data[:, indices], method=method,
                                                  n_clusters=n_microstates, random_state=seed, **kwargs)
    microstates_list.append(microstates)

#        # Predict
#        segmentation = _modified_kmeans_predict(data, microstates)
#        segmentation_list.append(segmentation)

    # Select best run with highest global explained variance (GEV) or cross-validation criterion
    segmentation = _modified_kmeans_predict(data, microstates)  # needs to be changed
    segmentation_list.append(segmentation)
    gev = microstates_gev(data, microstates, segmentation, gfp_sum_sq)
    gev_list.append(gev)

    cv = microstates_crossvalidation(data, microstates, gfp,
                                     n_channels=data.shape[0], n_samples=data.shape[1])
    cv_list.append(cv)

    # Select optimal
    if criterion == 'gev':
        optimal = np.argmax(gev_list)
    elif criterion == 'cv':
        optimal = np.argmin(cv_list)

    best_microstates = microstates_list[optimal]
    best_segmentation = segmentation_list[optimal]
    best_gev = gev_list[optimal]
    best_cv = cv_list[optimal]

#        if gev > best_gev:
#            best_gev, best_microstates, best_segmentation = gev, microstates, segmentation

    # Prepare output
    out = {"Microstates": best_microstates,
           "Sequence": best_segmentation,
           "GEV": best_gev,
           "GFP": gfp,
           "Cross-Validation Criterion": best_cv,
           "Info": info}

    # Reorder
    out = microstates_classify(out)

    return microstates, segmentation, out


## =============================================================================
## Clustering algorithms
## =============================================================================
def _modified_kmeans_predict(data, microstates):
    """Back-fit kmeans clustering on data
    """
    activation = microstates.dot(data)
    segmentation = np.argmax(np.abs(activation), axis=0)
    return segmentation
