__authors__ = "Ian Goodfellow"
__copyright__ = "Copyright 2011, Universite de Montreal"
__credits__ = ["Ian Goodfellow"]
__license__ = "3-clause BSD"
__maintainer__ = "Ian Goodfellow"


import time
from pylearn2.models.model import Model
from theano import config, function, shared
import theano.tensor as T
import numpy as np
import warnings
from theano.printing import Print
from theano import map
from theano.gof.op import get_debug_values, debug_error_message
from pylearn2.utils import make_name, sharedX, as_floatX
from pylearn2.monitor import Monitor

warnings.warn('s3c changing the recursion limit')
import sys
sys.setrecursionlimit(50000)

def numpy_norms(W):
    """ returns a vector containing the L2 norm of each
    column of W, where W and the return value are
    numpy ndarrays """
    return np.sqrt(1e-8+np.square(W).sum(axis=0))

def theano_norms(W):
    """ returns a vector containing the L2 norm of each
    column of W, where W and the return value are symbolic
    theano variables """
    return T.sqrt(as_floatX(1e-8)+T.sqr(W).sum(axis=0))

def rotate_towards(old_W, new_W, new_coeff):
    """
        old_W: every column is a unit vector

        for each column, rotates old_w toward
            new_w by new_coeff * theta where
            theta is the angle between them
    """

    norms = theano_norms(new_W)

    #update, scaled back onto unit sphere
    scal_points = new_W / norms.dimshuffle('x',0)

    #dot product between scaled update and current W
    dot_update = (old_W * scal_points).sum(axis=0)

    theta = T.arccos(dot_update)

    rot_amt = new_coeff * theta

    new_basis_dir = scal_points - dot_update * old_W

    new_basis_norms = theano_norms(new_basis_dir)

    new_basis = new_basis_dir / new_basis_norms

    rval = T.cos(rot_amt) * old_W + T.sin(rot_amt) * new_basis

    return rval

def full_min(var):
    """ returns a symbolic expression for the value of the minimal
    element of symbolic tensor. T.min does something else as of
    the time of this writing. """
    return var.min(axis=range(0,len(var.type.broadcastable)))

def full_max(var):
    """ returns a symbolic expression for the value of the maximal
        element of a symbolic tensor. T.max does something else as of the
        time of this writing. """
    return var.max(axis=range(0,len(var.type.broadcastable)))

class SufficientStatistics:
    """ The SufficientStatistics class computes several sufficient
        statistics of a minibatch of examples / variational parameters.
        This is mostly for convenience since several expressions are
        easy to express in terms of these same sufficient statistics.
        Also, re-using the same expression for the sufficient statistics
        in multiple code locations can reduce theano compilation time.
        The current version of the S3C code no longer supports features
        like decaying sufficient statistics since these were not found
        to be particularly beneficial relative to the burden of computing
        the O(nhid^2) second moment matrix. The current version of the code
        merely computes the sufficient statistics apart from the second
        moment matrix as a notational convenience. Expressions that most
        naturally are expressed in terms of the second moment matrix
        are now written with a different order of operations that
        avoids O(nhid^2) operations but whose dependence on the dataset
        cannot be expressed in terms only of sufficient statistics."""


    def __init__(self, d):
        self. d = {}
        for key in d:
            self.d[key] = d[key]

    @classmethod
    def from_observations(self, needed_stats, V, H_hat, S_hat, var_s0_hat, var_s1_hat):
        """
            returns a SufficientStatistics

            needed_stats: a set of string names of the statistics to include

            V: a num_examples x nvis matrix of input examples
            H_hat: a num_examples x nhid matrix of \hat{h} variational parameters
            S_hat: variational parameters for expectation of s given h=1
            var_s0_hat: variational parameters for variance of s given h=0
                        (only a vector of length nhid, since this is the same for
                        all inputs)
            var_s1_hat: variational parameters for variance of s given h=1
                        (again, a vector of length nhid)
        """

        m = T.cast(V.shape[0],config.floatX)

        H_name = make_name(H_hat, 'anon_H_hat')
        Mu1_name = make_name(S_hat, 'anon_S_hat')

        #mean_h
        assert H_hat.dtype == config.floatX
        mean_h = T.mean(H_hat, axis=0)
        assert H_hat.dtype == mean_h.dtype
        assert mean_h.dtype == config.floatX
        mean_h.name = 'mean_h('+H_name+')'

        #mean_v
        mean_v = T.mean(V,axis=0)

        #mean_sq_v
        mean_sq_v = T.mean(T.sqr(V),axis=0)

        #mean_s1
        mean_s1 = T.mean(S_hat,axis=0)

        #mean_sq_s
        mean_sq_S = H_hat * (var_s1_hat + T.sqr(S_hat)) + (1. - H_hat)*(var_s0_hat)
        mean_sq_s = T.mean(mean_sq_S,axis=0)

        #mean_hs
        mean_HS = H_hat * S_hat
        mean_hs = T.mean(mean_HS,axis=0)
        mean_hs.name = 'mean_hs(%s,%s)' % (H_name, Mu1_name)
        mean_s = mean_hs #this here refers to the expectation of the s variable, not s_hat
        mean_D_sq_mean_Q_hs = T.mean(T.sqr(mean_HS), axis=0)

        #mean_sq_hs
        mean_sq_HS = H_hat * (var_s1_hat + T.sqr(S_hat))
        mean_sq_hs = T.mean(mean_sq_HS, axis=0)
        mean_sq_hs.name = 'mean_sq_hs(%s,%s)' % (H_name, Mu1_name)

        #mean_sq_mean_hs
        mean_sq_mean_hs = T.mean(T.sqr(mean_HS), axis=0)
        mean_sq_mean_hs.name = 'mean_sq_mean_hs(%s,%s)' % (H_name, Mu1_name)

        #mean_hsv
        sum_hsv = T.dot(mean_HS.T,V)
        sum_hsv.name = 'sum_hsv<from_observations>'
        mean_hsv = sum_hsv / m


        d = {
                    "mean_h"                :   mean_h,
                    "mean_v"                :   mean_v,
                    "mean_sq_v"             :   mean_sq_v,
                    "mean_s"                :   mean_s,
                    "mean_s1"               :   mean_s1,
                    "mean_sq_s"             :   mean_sq_s,
                    "mean_hs"               :   mean_hs,
                    "mean_sq_hs"            :   mean_sq_hs,
                    "mean_sq_mean_hs"       :   mean_sq_mean_hs,
                    "mean_hsv"              :   mean_hsv,
                }


        final_d = {}

        for stat in needed_stats:
            final_d[stat] = d[stat]
            final_d[stat].name = 'observed_'+stat

        return SufficientStatistics(final_d)


class S3C(Model):

    def __init__(self, nvis, nhid, irange, init_bias_hid,
                       init_B, min_B, max_B,
                       init_alpha, min_alpha, max_alpha, init_mu,
                       e_step,
                       m_step,
                        min_bias_hid = -1e30,
                        max_bias_hid = 1e30,
                        min_mu = -1e30,
                        max_mu = 1e30,
                        tied_B = False,
                       monitor_stats = None,
                       monitor_params = None,
                       monitor_functional = False,
                       recycle_q = 0,
                       seed = None,
                       disable_W_update = False,
                       constrain_W_norm = False,
                       monitor_norms = False,
                       random_patches_src = None,
                       init_unit_W = None,
                       debug_m_step = False,
                       print_interval = 10000):
        """"
        nvis: # of visible units
        nhid: # of hidden units
        irange: (scalar) weights are initinialized ~U( [-irange,irange] )
        init_bias_hid: initial value of hidden biases (scalar or vector)
        init_B: initial value of B (scalar or vector)
        min_B, max_B: (scalar) learning updates to B are clipped to [min_B, max_B]
        init_alpha: initial value of alpha (scalar or vector)
        min_alpha, max_alpha: (scalar) learning updates to alpha are clipped to [min_alpha, max_alpha]
        init_mu: initial value of mu (scalar or vector)
        min_mu/max_mu: clip mu updates to this range.
        e_step:      An E_Step object that determines what kind of E-step to do
        m_step:      An M_Step object that determines what kind of M-step to do
        tied_B:         if True, use a scalar times identity for the precision on visible units.
                        otherwise use a diagonal matrix for the precision on visible units
        constrain_W_norm: if true, norm of each column of W must be 1 at all times
        init_unit_W:      if true, each column of W is initialized to have unit norm
        monitor_stats:  a list of sufficient statistics to monitor on the monitoring dataset
        monitor_params: a list of parameters to monitor TODO: push this into Model base class
        monitor_functional: if true, monitors the EM functional on the monitoring dataset
        monitor_norms: if true, monitors the norm of W at the end of each solve step, but before
                        blending with old W by new_coeff
                        This lets us see how much distortion is introduced by norm clipping
                        Note that unless new_coeff = 1, the post-solve norm monitored by this
                        flag will not be equal to the norm of the final parameter value, even
                        if no norm clipping is activated.
        recycle_q: if nonzero, initializes the e-step with the output of the previous iteration's
                    e-step. obviously this should only be used if you are using the same data
                    in each batch. when recycle_q is nonzero, it should be set to the batch size.
        disable_W_update: if true, doesn't update W (useful for experiments where you only learn the prior)
        random_patches_src: if not None, should be a dataset
                            will set W to a batch
        init_unit_W:   if True, initializes weights with unit norm
        """

        super(S3C,self).__init__()

        self.debug_m_step = debug_m_step


        if init_unit_W is not None and not init_unit_W:
            assert not constrain_W_norm

        if random_patches_src is not None:
            self.init_W = random_patches_src.get_batch_design(nhid).T
        else:
            self.init_W = None

        if monitor_stats is None:
            self.monitor_stats = []
        else:
            self.monitor_stats = [ elem for elem in monitor_stats ]

        if monitor_params is None:
            self.monitor_params = []
        else:
            self.monitor_params = [ elem for elem in monitor_params ]

        self.init_unit_W = init_unit_W

        self.seed = seed

        self.print_interval = print_interval

        self.constrain_W_norm = constrain_W_norm

        self.monitor_norms = monitor_norms
        self.disable_W_update = disable_W_update
        self.monitor_functional = monitor_functional
        self.nvis = nvis
        self.nhid = nhid
        self.irange = irange
        self.init_bias_hid = init_bias_hid
        self.init_alpha = float(init_alpha)
        self.min_alpha = float(min_alpha)
        self.max_alpha = float(max_alpha)
        self.init_B = float(init_B)
        self.min_B = float(min_B)
        self.max_B = float(max_B)
        self.e_step = e_step
        self.e_step.register_model(self)
        self.m_step = m_step
        self.init_mu = init_mu
        self.min_mu = np.cast[config.floatX](float(min_mu))
        self.max_mu = np.cast[config.floatX](float(max_mu))
        self.min_bias_hid = min_bias_hid
        self.max_bias_hid = max_bias_hid
        self.recycle_q = recycle_q
        self.tied_B = tied_B

        self.reset_rng()

        self.redo_everything()


    def set_dtype(self, dtype):
        for field in dir(self):
            obj = getattr(self, field)
            if hasattr(obj, 'get_value'):
                setattr(self, field, shared(np.cast[dtype](obj.get_value())))

    def reset_rng(self):
        if self.seed is None:
            self.rng = np.random.RandomState([1.,2.,3.])
        else:
            self.rng = np.random.RandomState(self.seed)

    def redo_everything(self):

        if self.init_W is not None:
            W = self.init_W.copy()
        else:
            W = self.rng.uniform(-self.irange, self.irange, (self.nvis, self.nhid))

        if self.constrain_W_norm or self.init_unit_W:
            norms = numpy_norms(W)
            W /= norms

        self.W = sharedX(W, name = 'W')
        self.bias_hid = sharedX(np.zeros(self.nhid)+self.init_bias_hid, name='bias_hid')
        self.alpha = sharedX(np.zeros(self.nhid)+self.init_alpha, name = 'alpha')
        self.mu = sharedX(np.zeros(self.nhid)+self.init_mu, name='mu')
        if self.tied_B:
            self.B_driver = sharedX(0.0+self.init_B, name='B')
        else:
            self.B_driver = sharedX(np.zeros(self.nvis)+self.init_B, name='B')

        self.test_batch_size = 2

        if self.recycle_q:
            self.prev_H = sharedX(np.zeros((self.recycle_q,self.nhid)), name="prev_H")
            self.prev_Mu1 = sharedX(np.zeros((self.recycle_q,self.nhid)), name="prev_Mu1")

        if self.debug_m_step:
            warnings.warn('M step debugging activated-- this is only valid for certain settings, and causes a performance slowdown.')
            self.em_functional_diff = sharedX(0.)

        self.censored_updates = {}
        self.register_names_to_del(['censored_updates'])
        for param in self.get_params():
            self.censored_updates[param] = set([])

        if self.monitor_norms:
            self.debug_norms = sharedX(np.zeros(self.nhid))

        self.redo_theano()

    def em_functional(self, H_hat, S_hat, var_s0_hat, var_s1_hat, stats):
        """ Returns the em_functional for a single batch of data
            stats is assumed to be computed from and only from
            the same data points that yielded H """

        entropy_term = (self.entropy_hs(H_hat = H_hat, var_s0_hat = var_s0_hat, var_s1_hat = var_s1_hat)).mean()
        likelihood_term = self.expected_log_prob_vhs(stats, H_hat = H_hat, S_hat = S_hat)

        em_functional = likelihood_term + entropy_term

        return em_functional

    def get_monitoring_channels(self, V):
            try:
                self.compile_mode()

                rval = self.m_step.get_monitoring_channels(V, self)

                from_e_step = self.e_step.get_monitoring_channels(V, self)

                rval.update(from_e_step)

                if self.debug_m_step:
                    rval['m_step_diff'] = self.em_functional_diff

                monitor_stats = len(self.monitor_stats) > 0

                if monitor_stats or self.monitor_functional:

                    obs = self.e_step.variational_inference(V)

                    needed_stats = set(self.monitor_stats)

                    if self.monitor_functional:
                        needed_stats = needed_stats.union(S3C.expected_log_prob_vhs_needed_stats())

                    stats = SufficientStatistics.from_observations( needed_stats = needed_stats,
                                                                V = V, ** obs )

                    H_hat = obs['H_hat']
                    S_hat = obs['S_hat']
                    var_s0_hat = obs['var_s0_hat']
                    var_s1_hat = obs['var_s1_hat']

                    if self.monitor_functional:
                        em_functional = self.em_functional(H_hat = H_hat, S_hat = S_hat, var_s0_hat = var_s0_hat,
                                var_s1_hat = var_s1_hat, stats = stats)

                        rval['em_functional'] = em_functional

                    if monitor_stats:
                        for stat in self.monitor_stats:
                            stat_val = stats.d[stat]

                            rval[stat+'_min'] = T.min(stat_val)
                            rval[stat+'_mean'] = T.mean(stat_val)
                            rval[stat+'_max'] = T.max(stat_val)
                        #end for stat
                    #end if monitor_stats
                #end if monitor_stats or monitor_functional

                if len(self.monitor_params) > 0:
                    for param in self.monitor_params:
                        param_val = getattr(self, param)


                        rval[param+'_min'] = full_min(param_val)
                        rval[param+'_mean'] = T.mean(param_val)

                        mx = full_max(param_val)
                        assert len(mx.type.broadcastable) == 0
                        rval[param+'_max'] = mx

                        if param == 'mu':
                            abs_mu = abs(self.mu)
                            rval['mu_abs_min'] = full_min(abs_mu)
                            rval['mu_abs_mean'] = T.mean(abs_mu)
                            rval['mu_abs_max'] = full_max(abs_mu)

                        if param == 'W':
                            norms = theano_norms(self.W)
                            rval['W_norm_min'] = full_min(norms)
                            rval['W_norm_mean'] = T.mean(norms)
                            rval['W_norm_max'] = T.max(norms)

                if self.monitor_norms:
                    rval['post_solve_norms_min'] = T.min(self.debug_norms)
                    rval['post_solve_norms_max'] = T.max(self.debug_norms)
                    rval['post_solve_norms_mean'] = T.mean(self.debug_norms)

                return rval
            finally:
                self.deploy_mode()

    def compile_mode(self):
        """ If any shared variables need to have batch-size dependent sizes,
        sets them all to the sizes used for interactive debugging during graph construction """
        if self.recycle_q:
            self.prev_H.set_value(
                    np.cast[self.prev_H.dtype](
                        np.zeros((self.test_batch_size, self.nhid)) \
                                + 1./(1.+np.exp(-self.bias_hid.get_value()))))
            self.prev_Mu1.set_value(
                    np.cast[self.prev_Mu1.dtype](
                        np.zeros((self.test_batch_size, self.nhid)) + self.mu.get_value() ) )

    def deploy_mode(self):
        """ If any shared variables need to have batch-size dependent sizes, sets them all to their runtime sizes """
        if self.recycle_q:
            self.prev_H.set_value( np.cast[self.prev_H.dtype]( np.zeros((self.recycle_q, self.nhid)) + 1./(1.+np.exp(-self.bias_hid.get_value()))))
            self.prev_Mu1.set_value( np.cast[self.prev_Mu1.dtype]( np.zeros((self.recycle_q, self.nhid)) + self.mu.get_value() ) )

    def get_params(self):
        return [self.W, self.bias_hid, self.alpha, self.mu, self.B_driver ]

    def energy_vhs(self, V, H, S):
        " H MUST be binary "

        h_term = - T.dot(H, self.bias_hid)
        assert len(h_term.type.broadcastable) == 1

        s_term_1 = T.dot(T.sqr(S), self.alpha)/2.
        s_term_2 = -T.dot(S * self.mu * H , self.alpha)
        #s_term_3 = T.dot(T.sqr(self.mu * H), self.alpha)/2.
        s_term_3 = T.dot(T.sqr(self.mu) * H, self.alpha) / 2.

        s_term = s_term_1 + s_term_2 + s_term_3
        #s_term = T.dot( T.sqr( S - self.mu * H) , self.alpha) / 2.
        assert len(s_term.type.broadcastable) == 1


        recons = T.dot(H*S, self.W.T)

        v_term_1 = T.dot( T.sqr(V), self.B) / 2.
        v_term_2 = T.dot( - V * recons, self.B)
        v_term_3 = T.dot( T.sqr(recons), self.B) / 2.

        v_term = v_term_1 + v_term_2 + v_term_3

        #v_term = T.dot( T.sqr( V - recons), self. B) / 2.
        assert len(v_term.type.broadcastable) == 1

        rval = h_term + s_term + v_term
        assert len(rval.type.broadcastable) == 1

        return rval

    def expected_energy_vhs(self, V, H_hat, S_hat, var_s0_hat, var_s1_hat):
        """ This is not the same as negative expected log prob,
        which includes the constant term for the log partition function """

        var_HS = H_hat * var_s1_hat + (1.-H_hat) * var_s0_hat

        half = as_floatX(.5)

        HS = H_hat * S_hat

        sq_HS = H_hat * ( var_s1_hat + T.sqr(S_hat))

        sq_S = sq_HS + (1.-H_hat)*(var_s0_hat)

        presign = T.dot(H_hat, self.bias_hid)
        presign.name = 'presign'
        h_term = - presign
        assert len(h_term.type.broadcastable) == 1

        precoeff =  T.dot(sq_S, self.alpha)
        precoeff.name = 'precoeff'
        s_term_1 = half * precoeff
        assert len(s_term_1.type.broadcastable) == 1

        presign2 = T.dot(HS, self.alpha * self.mu)
        presign2.name = 'presign2'
        s_term_2 = - presign2
        assert len(s_term_2.type.broadcastable) == 1

        s_term_3 = half * T.dot(H_hat, T.sqr(self.mu) * self.alpha)
        assert len(s_term_3.type.broadcastable) == 1

        s_term = s_term_1 + s_term_2 + s_term_3

        v_term_1 = half * T.dot(T.sqr(V),self.B)
        assert len(v_term_1.type.broadcastable) == 1

        term6_factor1 = V * self.B
        term6_factor2 = T.dot(HS, self.W.T)
        v_term_2 = - (term6_factor1 * term6_factor2).sum(axis=1)
        assert len(v_term_2.type.broadcastable) == 1

        term7_subterm1 = T.dot(T.sqr(T.dot(HS, self.W.T)), self.B)
        assert len(term7_subterm1.type.broadcastable) == 1
        #term7_subterm2 = T.dot(var_HS, self.w)
        term7_subterm2 = - T.dot( T.dot(T.sqr(HS), T.sqr(self.W.T)), self.B)
        term7_subterm3 = T.dot( T.dot(sq_HS, T.sqr(self.W.T)), self.B )

        #v_term_3 = half * (term7_subterm1 + term7_subterm2)
        v_term_3 = half * (term7_subterm1 + term7_subterm2 + term7_subterm3)
        assert len(v_term_3.type.broadcastable) == 1

        v_term = v_term_1 + v_term_2 + v_term_3

        rval = h_term + s_term + v_term

        return rval

    def entropy_h(self, H_hat):

        #TODO: replace with actually evaluating 0 log 0 as 0
        #note: can't do 1e-8, 1.-1e-8 rounds to 1.0 in float32
        H_hat = T.clip(H_hat, 1e-7, 1.-1e-7)

        logH = T.log(H_hat)

        logOneMinusH = T.log(1.-H_hat)

        term1 = - T.sum( H_hat * logH , axis=1)
        assert len(term1.type.broadcastable) == 1

        term2 = - T.sum( (1.-H_hat) * logOneMinusH , axis =1 )
        assert len(term2.type.broadcastable) == 1

        rval = term1 + term2

        return rval

    def entropy_hs(self, H_hat, var_s0_hat, var_s1_hat):

        half = as_floatX(.5)

        one = as_floatX(1.)

        two = as_floatX(2.)

        pi = as_floatX(np.pi)

        term1_plus_term2 = self.entropy_h(H_hat)
        assert len(term1_plus_term2.type.broadcastable) == 1

        term3 = T.sum( H_hat * ( half * (T.log(var_s1_hat) +  T.log(two*pi) + one )  ) , axis= 1)
        assert len(term3.type.broadcastable) == 1

        term4 = T.dot( 1.-H_hat, half * (T.log(var_s0_hat) +  T.log(two*pi) + one ))
        assert len(term4.type.broadcastable) == 1

        rval = term1_plus_term2 + term3 + term4

        return rval


    def make_learn_func(self, V):
        """
        V: a symbolic design matrix
        """

        #E step
        hidden_obs = self.e_step.variational_inference(V)

        stats = SufficientStatistics.from_observations(needed_stats = self.m_step.needed_stats(),
                V = V, **hidden_obs)

        H_hat = hidden_obs['H_hat']
        S_hat = hidden_obs['S_hat']

        learning_updates = self.m_step.get_updates(self, stats, H_hat, S_hat)

        if self.recycle_q:
            learning_updates[self.prev_H] = H_hat
            learning_updates[self.prev_Mu1] = S_hat

        self.censor_updates(learning_updates)

        if self.debug_m_step:
            em_functional_before = self.em_functional(H = hidden_obs['H'],
                                                      var_s0_hat = hidden_obs['var_s0_hat'],
                                                      var_s1_hat = hidden_obs['var_s1_hat'],
                                                      stats = stats)

            tmp_bias_hid = self.bias_hid
            tmp_mu = self.mu
            tmp_alpha = self.alpha
            tmp_W = self.W
            tmp_B_driver = self.B_driver

            self.bias_hid = learning_updates[self.bias_hid]
            self.mu = learning_updates[self.mu]
            self.alpha = learning_updates[self.alpha]
            if self.W in learning_updates:
                self.W = learning_updates[self.W]
            self.B_driver = learning_updates[self.B_driver]
            self.make_pseudoparams()

            try:
                em_functional_after  = self.em_functional(H_hat = hidden_obs['H_hat'],
                                                          var_s0_hat = hidden_obs['var_s0_hat'],
                                                          var_s1_hat = hidden_obs['var_s1_hat'],
                                                          stats = stats)
            finally:
                self.bias_hid = tmp_bias_hid
                self.mu = tmp_mu
                self.alpha = tmp_alpha
                self.W = tmp_W
                self.B_driver = tmp_B_driver
                self.make_pseudoparams()

            em_functional_diff = em_functional_after - em_functional_before

            learning_updates[self.em_functional_diff] = em_functional_diff



        print "compiling function..."
        t1 = time.time()
        rval = function([V], updates = learning_updates)
        t2 = time.time()
        print "... compilation took "+str(t2-t1)+" seconds"
        print "graph size: ",len(rval.maker.env.toposort())

        return rval

    def censor_updates(self, updates):

        def should_censor(param):
            return param in updates and updates[param] not in self.censored_updates[param]

        if should_censor(self.W):
            if self.disable_W_update:
                del updates[self.W]
            elif self.constrain_W_norm:
                norms = theano_norms(updates[self.W])
                updates[self.W] /= norms.dimshuffle('x',0)

        if should_censor(self.alpha):
            updates[self.alpha] = T.clip(updates[self.alpha],self.min_alpha,self.max_alpha)

        if should_censor(self.mu):
            updates[self.mu] = T.clip(updates[self.mu],self.min_mu,self.max_mu)

        if should_censor(self.B_driver):
            updates[self.B_driver] = T.clip(updates[self.B_driver],self.min_B,self.max_B)

        if should_censor(self.bias_hid):
            updates[self.bias_hid] = T.clip(updates[self.bias_hid],self.min_bias_hid,self.max_bias_hid)

        model_params = self.get_params()
        for param in updates:
            if param in model_params:
                self.censored_updates[param] = self.censored_updates[param].union(set([updates[param]]))


    def random_design_matrix(self, batch_size, theano_rng,
                            H_sample = None):
        """
            H_sample: a matrix of values of H
                      if none is provided, samples one from the prior
                      (H_sample is used if you want to see what samples due
                        to specific hidden units look like, or when sampling
                        from a larger model that s3c is part of)
        """

        if not hasattr(self,'p'):
            self.make_pseudoparams()

        hid_shape = (batch_size, self.nhid)

        if H_sample is None:
            H_sample = theano_rng.binomial( size = hid_shape, n = 1, p = self.p)

        pos_s_sample = theano_rng.normal( size = hid_shape, avg = self.mu, std = T.sqrt(1./self.alpha) )

        final_hs_sample = H_sample * pos_s_sample

        V_mean = T.dot(final_hs_sample, self.W.T)

        warnings.warn('showing conditional means on visible units rather than true samples')
        return V_mean

        V_sample = theano_rng.normal( size = V_mean.shape, avg = V_mean, std = self.B)

        return V_sample


    @classmethod
    def expected_log_prob_vhs_needed_stats(cls):
        h = S3C.log_likelihood_h_needed_stats()
        s = S3C.log_likelihood_s_given_h_needed_stats()
        v = S3C.expected_log_prob_v_given_hs_needed_stats()

        union = h.union(s).union(v)

        return union


    def expected_log_prob_vhs(self, stats, H_hat, S_hat):

        expected_log_prob_v_given_hs = self.expected_log_prob_v_given_hs(stats, H_hat = H_hat, S_hat = S_hat)
        log_likelihood_s_given_h  = self.log_likelihood_s_given_h(stats)
        log_likelihood_h          = self.log_likelihood_h(stats)

        rval = expected_log_prob_v_given_hs + log_likelihood_s_given_h + log_likelihood_h

        assert len(rval.type.broadcastable) == 0

        return rval


    def log_prob_v_given_hs(self, V, H, Mu1):
        """
        V, H, Mu1 are SAMPLES   (i.e., H must be LITERALLY BINARY)
        Return value is a vector, of length batch size
        """

        half = as_floatX(0.5)
        two = as_floatX(2.)
        pi = as_floatX(np.pi)
        N = as_floatX(self.nhid)

        term1 = half * T.sum(T.log(self.B))
        term2 = - half * N * T.log(two * pi)

        mean_HS = H * Mu1
        recons = T.dot(H*Mu1, self.W.T)
        residuals = V - recons


        term3 = - half * T.dot(T.sqr(residuals), self.B)

        rval = term1 + term2 + term3

        assert len(rval.type.broadcastable) == 1

        return rval

    @classmethod
    def expected_log_prob_v_given_hs_needed_stats(cls):
        return set(['mean_sq_v','mean_hsv', 'mean_sq_hs', 'mean_sq_mean_hs'])

    def expected_log_prob_v_given_hs(self, stats, H_hat, S_hat):
        """
        Return value is a SCALAR-- expectation taken across batch index too
        """


        """
        E_v,h,s \sim Q log P( v | h, s)
        = sum_k [  E_v,h,s \sim Q log sqrt(B/2 pi) exp( - 0.5 B (v- W[v,:] (h*s) )^2)   ]
        = sum_k [ E_v,h,s \sim Q 0.5 log B_k - 0.5 log 2 pi - 0.5 B_k v_k^2 + v_k B_k W[k,:] (h*s) - 0.5 B_k sum_i sum_j W[k,i] W[k,j] h_i s_i h_j s_j ]
        = sum_k [ 0.5 log B_k - 0.5 log 2 pi - 0.5 B_k v_k^2 + v_k B_k W[k,:] (h*s) ] - 0.5  sum_k B_k sum_i,j W[k,i] W[k,j]  < h_i s_i  h_j s_j >
        = sum_k [ 0.5 log B_k - 0.5 log 2 pi - 0.5 B_k v_k^2 + v_k B_k W[k,:] (h*s) ] - (1/2T)  sum_k B_k sum_i,j W[k,i] W[k,j]  sum_t <h_it s_it  h_jt s_t>
        = sum_k [ 0.5 log B_k - 0.5 log 2 pi - 0.5 B_k v_k^2 + v_k B_k W[k,:] (h*s) ] - (1/2T)  sum_k B_k sum_t sum_i,j W[k,i] W[k,j] <h_it s_it  h_jt s_t>
        = sum_k [ 0.5 log B_k - 0.5 log 2 pi - 0.5 B_k v_k^2 + v_k B_k W[k,:] (h*s) ]
          - (1/2T)  sum_k B_k sum_t sum_i W[k,i]  sum_{j\neq i} W[k,j] <h_it s_it>  <h_jt s_t>
          - (1/2T) sum_k B_k sum_t sum_i W[k,i]^2 <h_it s_it^2>
        = sum_k [ 0.5 log B_k - 0.5 log 2 pi - 0.5 B_k v_k^2 + v_k B_k W[k,:] (h*s) ]
          - (1/2T)  sum_k B_k sum_t sum_i W[k,i] <h_it s_it> sum_j W[k,j]  <h_jt s_t>
          + (1/2T) sum_k B_k sum_t sum_i W[k,i]^2 <h_it s_it>^2
          - (1/2T) sum_k B_k sum_t sum_i W[k,i]^2 <h_it s_it^2>
        = sum_k [ 0.5 log B_k - 0.5 log 2 pi - 0.5 B_k v_k^2 + v_k B_k W[k,:] (h*s) ]
          - (1/2T)  sum_k B_k sum_t sum_i W[k,i] <h_it s_it> sum_j W[k,j]  <h_jt s_t>
          + (1/2T) sum_k B_k sum_t sum_i W[k,i]^2 (<h_it s_it>^2 - <h_it s_it^2>)
        = sum_k [ 0.5 log B_k - 0.5 log 2 pi - 0.5 B_k v_k^2 + v_k B_k W[k,:] (h*s) ]
          - (1/2T)  sum_k B_k sum_t sum_i W_ki HS_it sum_j W_kj  HS_tj
          + (1/2T) sum_k B_k sum_t sum_i sq(W)_ki ( sq(HS)-sq_HS)_it
        = sum_k [ 0.5 log B_k - 0.5 log 2 pi - 0.5 B_k v_k^2 + v_k B_k W[k,:] (h*s) ]
          - (1/2T)  sum_k B_k sum_t sum_i W_ki HS_it sum_j W_kj  HS_tj
          + (1/2T) sum_k B_k sum_t sum_i sq(W)_ki ( sq(HS)-sq_HS)_it
        = sum_k [ 0.5 log B_k - 0.5 log 2 pi - 0.5 B_k v_k^2 + v_k B_k W[k,:] (h*s) ]
          - (1/2T)  sum_k B_k sum_t sum_i W_ki HS_it sum_j W_kj  HS_tj
          + (1/2T) sum_k B_k sum_t sum_i sq(W)_ki ( sq(HS)-sq_HS)_it
        = sum_k [ 0.5 log B_k - 0.5 log 2 pi - 0.5 B_k v_k^2 + v_k B_k W[k,:] (h*s) ]
          - (1/2T)  sum_k B_k sum_t (HS_t: W_k:^T)  (HS_t:  W_k:^T)
          + (1/2) sum_k B_k  sum_i sq(W)_ki ( mean_sq_mean_hs-mean_sq_hs)_i
        = sum_k [ 0.5 log B_k - 0.5 log 2 pi - 0.5 B_k v_k^2 + v_k B_k W[k,:] (h*s) ]
          - (1/2T)  sum_t sum_k B_k  (HS_t: W_k:^T)^2
          + (1/2) sum_k B_k  sum_i sq(W)_ki ( mean_sq_mean_hs-mean_sq_hs)_i
        = sum_k [ 0.5 log B_k - 0.5 log 2 pi - 0.5 B_k v_k^2 + v_k B_k W[k,:] (h*s) ]
          - (1/2)  mean(   (HS W^T)^2 B )
          + (1/2) sum_k B_k  sum_i sq(W)_ki ( mean_sq_mean_hs-mean_sq_hs)_i
        """


        half = as_floatX(0.5)
        two = as_floatX(2.)
        pi = as_floatX(np.pi)
        N = as_floatX(self.nhid)

        mean_sq_v = stats.d['mean_sq_v']
        mean_hsv  = stats.d['mean_hsv']
        mean_sq_mean_hs = stats.d['mean_sq_mean_hs']
        mean_sq_hs = stats.d['mean_sq_hs']

        term1 = half * T.sum(T.log(self.B))
        term2 = - half * N * T.log(two * pi)
        term3 = - half * T.dot(self.B, mean_sq_v)
        term4 = T.dot(self.B , (self.W * mean_hsv.T).sum(axis=1))

        HS = H_hat * S_hat
        recons = T.dot(HS, self.W.T)
        sq_recons = T.sqr(recons)
        weighted = T.dot(sq_recons, self.B)
        assert len(weighted.type.broadcastable) == 1
        term5 = - half * T.mean( weighted)

        term6 = half * T.dot(self.B, T.dot(T.sqr(self.W), mean_sq_mean_hs - mean_sq_hs))

        rval = term1 + term2 + term3 + term4 + term5 + term6

        assert len(rval.type.broadcastable) == 0

        return rval

    @classmethod
    def log_likelihood_s_given_h_needed_stats(cls):
        return set(['mean_h','mean_hs','mean_sq_s'])

    def log_likelihood_s_given_h(self, stats):

        """
        E_h,s\sim Q log P(s|h)
        = E_h,s\sim Q log sqrt( alpha / 2pi) exp(- 0.5 alpha (s-mu h)^2)
        = E_h,s\sim Q log sqrt( alpha / 2pi) - 0.5 alpha (s-mu h)^2
        = E_h,s\sim Q  0.5 log alpha - 0.5 log 2 pi - 0.5 alpha s^2 + alpha s mu h + 0.5 alpha mu^2 h^2
        = E_h,s\sim Q 0.5 log alpha - 0.5 log 2 pi - 0.5 alpha s^2 + alpha mu h s + 0.5 alpha mu^2 h
        = 0.5 log alpha - 0.5 log 2 pi - 0.5 alpha mean_sq_s + alpha mu mean_hs - 0.5 alpha mu^2 mean_h
        """

        mean_h = stats.d['mean_h']
        mean_sq_s = stats.d['mean_sq_s']
        mean_hs = stats.d['mean_hs']

        half = as_floatX(0.5)
        two = as_floatX(2.)
        N = as_floatX(self.nhid)
        pi = as_floatX(np.pi)

        term1 = half * T.log( self.alpha ).sum()
        term2 = - half * N * T.log(two*pi)
        term3 = - half * T.dot( self.alpha , mean_sq_s )
        term4 = T.dot(self.mu*self.alpha,mean_hs)
        term5 = - half * T.dot(T.sqr(self.mu), self.alpha * mean_h)

        rval = term1 + term2 + term3 + term4 + term5

        assert len(rval.type.broadcastable) == 0

        return rval

    @classmethod
    def log_likelihood_h_needed_stats(cls):
        return set(['mean_h'])

    def log_likelihood_h(self, stats):
        """ Returns the expected log probability of the vector h
            under the model when the data is drawn according to
            stats
        """

        """
            E_h\sim Q log P(h)
            = E_h\sim Q log exp( bh) / (1+exp(b))
            = E_h\sim Q bh - softplus(b)
        """

        mean_h = stats.d['mean_h']

        term1 = T.dot(self.bias_hid, mean_h)
        term2 = - T.nnet.softplus(self.bias_hid).sum()

        rval = term1 + term2

        assert len(rval.type.broadcastable) == 0

        return rval


    def make_pseudoparams(self):
        if self.tied_B:
            #can't just use a dimshuffle; dot products involving B won't work
            #and because doing it this way makes the partition function multiply by nvis automatically
            self.B = self.B_driver + as_floatX(np.zeros(self.nvis))
        else:
            self.B = self.B_driver

        self.w = T.dot(self.B, T.sqr(self.W))

        self.p = T.nnet.sigmoid(self.bias_hid)

    def redo_theano(self):
        try:
            self.compile_mode()
            init_names = dir(self)

            self.make_pseudoparams()

            self.get_B_value = function([], self.B)

            X = T.matrix(name='V')
            X.tag.test_value = np.cast[config.floatX](self.rng.randn(self.test_batch_size,self.nvis))

            self.learn_func = self.make_learn_func(X)

            final_names = dir(self)

            self.register_names_to_del([name for name in final_names if name not in init_names])
        finally:
            self.deploy_mode()
    #

    def learn(self, dataset, batch_size):
        self.learn_mini_batch(dataset.get_batch_design(batch_size))
    #


    def learn_mini_batch(self, X):

        self.learn_func(X)

        if self.monitor.examples_seen % self.print_interval == 0:
            print ""
            b = self.bias_hid.get_value(borrow=True)
            assert not np.any(np.isnan(b))
            p = 1./(1.+np.exp(-b))
            print 'p: ',(p.min(),p.mean(),p.max())
            B = self.B_driver.get_value(borrow=True)
            assert not np.any(np.isnan(B))
            print 'B: ',(B.min(),B.mean(),B.max())
            mu = self.mu.get_value(borrow=True)
            assert not np.any(np.isnan(mu))
            print 'mu: ',(mu.min(),mu.mean(),mu.max())
            alpha = self.alpha.get_value(borrow=True)
            assert not np.any(np.isnan(alpha))
            print 'alpha: ',(alpha.min(),alpha.mean(),alpha.max())
            W = self.W.get_value(borrow=True)
            assert not np.any(np.isnan(W))
            print 'W: ',(W.min(),W.mean(),W.max())
            norms = numpy_norms(W)
            print 'W norms:',(norms.min(),norms.mean(),norms.max())

        if self.debug_m_step:
            if self.em_functional_diff.get_value() < 0.0:
                warnings.warn( "m step decreased the em functional" )
                if self.debug_m_step != 'warn':
                    quit(-1)

    #

    def get_weights_format(self):
        return ['v','h']

def reflection_clip(Mu1, new_Mu1, rho = 0.5):

    ceiling = full_max(abs(new_Mu1))

    positives = Mu1 > 0
    non_positives = 1. - positives
    negatives = Mu1 < 0
    non_negatives = 1. - negatives

    rval = T.clip(new_Mu1, - rho * positives * Mu1 - non_positives * ceiling, non_negatives * ceiling - rho * negatives * Mu1 )

    return rval

class E_Step:
    """ A variational E_step that works by running damped fixed point
        updates on a structured variation approximation to
        P(v,h,s) (i.e., we do not use any auxiliary variable).

        The structured variational approximation is:

            P(v,h,s) = \Pi_i Q_i (h_i, s_i)

        We alternate between updating the Q parameters over s in parallel and
        updating the q parameters over h in parallel.

        The h parameters are updated with a damping coefficient that is the same
        for all units but changes each time step, specified by the yaml file. The slab
        variables are updated with:
            optionally: a unit-specific damping designed to ensure stability
                        by preventing reflections from going too far away
                        from the origin.
            optionally: additional damping that is the same for all units but
                        changes each time step, specified by the yaml file

        The update equations were derived based on updating h_i independently,
        then updating s_i independently, even though it is possible to solve for
        a simultaneous update to h_i and s_I.

        This is because the damping is necessary for parallel updates to be reasonable,
        but damping the solution to s_i from the joint update makes the solution to h_i
        from the joint update no longer optimal.

        """

    def get_monitoring_channels(self, V, model):

        #TODO: update this to show updates to h_i and s_i in correct sequence

        rval = {}

        if self.monitor_kl or self.monitor_em_functional:
            obs_history = self.variational_inference(V, return_history = True)

            for i in xrange(1, 2 + len(self.h_new_coeff_schedule)):
                obs = obs_history[i-1]
                if self.monitor_kl:
                    rval['trunc_KL_'+str(i)] = self.truncated_KL(V, model, obs).mean()
                if self.monitor_em_functional:
                    rval['em_functional_'+str(i)] = self.em_functional(V, model, obs).mean()

        return rval


    def __init__(self, h_new_coeff_schedule,
                       s_new_coeff_schedule = None,
                       clip_reflections = False,
                       monitor_kl = False,
                       monitor_em_functional = False,
                       rho = 0.5):
        """Parameters
        --------------
        h_new_coeff_schedule:
            list of coefficients to put on the new value of h on each damped fixed point step
                    (coefficients on s are driven by a special formula)
            length of this list determines the number of fixed point steps
        s_new_coeff_schedule:
            list of coefficients to put on the new value of s on each damped fixed point step
                These are applied AFTER the reflection clipping, which can be seen as a form of
                per-unit damping
                s_new_coeff_schedule must have same length as h_new_coeff_schedule
                if s_new_coeff_schedule is not provided, it will be filled in with all ones,
                    i.e. it will default to no damping beyond the reflection clipping
        clip_reflections, rho : if clip_reflections is true, the update to Mu1[i,j] is
            bounded on one side by - rho * Mu1[i,j] and unbounded on the other side
        """

        if s_new_coeff_schedule is None:
            s_new_coeff_schedule = [ 1.0 for rho in h_new_coeff_schedule ]
        else:
            assert len(s_new_coeff_schedule) == len(h_new_coeff_schedule)

        self.s_new_coeff_schedule = s_new_coeff_schedule

        self.clip_reflections = clip_reflections
        self.h_new_coeff_schedule = h_new_coeff_schedule
        self.monitor_kl = monitor_kl
        self.monitor_em_functional = monitor_em_functional

        self.rho = as_floatX(rho)

        self.model = None

    def em_functional(self, V, model, obs):
        """ Return value is a scalar """
        #TODO: refactor so that this is shared between E-steps

        needed_stats = S3C.expected_log_prob_vhs_needed_stats()

        stats = SufficientStatistics.from_observations( needed_stats = needed_stats,
                                                        X = V, ** obs )

        H_hat = obs['H_hat']
        var_s0_hat = obs['var_s0_hat']
        var_s1_hat = obs['var_s1_hat']

        entropy_term = (model.entropy_hs(H_hat = H_hat, var_s0_hat = var_s0_hat, var_s1_hat = var_s1_hat)).mean()
        likelihood_term = model.expected_log_prob_vhs(stats)

        em_functional = entropy_term + likelihood_term

        return em_functional


    def register_model(self, model):
        self.model = model

    def truncated_KL(self, V, model, obs):
        """ KL divergence between variation and true posterior, dropping terms that don't
            depend on the variational parameters """

        H_hat = obs['H_hat']
        var_s0_hat = obs['var_s0_hat']
        var_s1_hat = obs['var_s1_hat']
        S_hat = obs['S_hat']

        entropy_term = - model.entropy_hs(H_hat = H_hat, var_s0_hat = var_s0_hat, var_s1_hat = var_s1_hat)
        energy_term = model.expected_energy_vhs(V, H_hat = H_hat, S_hat = S_hat,
                                        var_s0_hat = var_s0_hat, var_s1_hat = var_s1_hat)

        KL = entropy_term + energy_term

        return KL

    def init_H_hat(self, V):

        if self.model.recycle_q:
            rval = self.model.prev_H

            if config.compute_test_value != 'off':
                if rval.get_value().shape[0] != V.tag.test_value.shape[0]:
                    raise Exception('E step given wrong test batch size', rval.get_value().shape, V.tag.test_value.shape)
        else:
            #just use the prior
            value =  T.nnet.sigmoid(self.model.bias_hid)
            rval = T.alloc(value, V.shape[0], value.shape[0])

            for rval_value, V_value in get_debug_values(rval, V):
                if rval_value.shape[0] != V_value.shape[0]:
                    debug_error_message("rval.shape = %s, V.shape = %s, element 0 should match but doesn't", str(rval_value.shape), str(V_value.shape))

        return rval

    def init_S_hat(self, V):
        if self.model.recycle_q:
            rval = self.model.prev_Mu1
        else:
            #just use the prior
            value = self.model.mu
            rval = T.alloc(value, V.shape[0], value.shape[0])

        return rval

    def infer_S_hat(self, V, H_hat, S_hat):

        H = H_hat
        Mu1 = S_hat

        for Vv, Hv in get_debug_values(V, H):
            if Vv.shape != (self.model.test_batch_size,self.model.nvis):
                raise Exception('Well this is awkward. We require visible input test tags to be of shape '+str((self.model.test_batch_size,self.model.nvis))+' but the monitor gave us something of shape '+str(Vv.shape)+". The batch index part is probably only important if recycle_q is enabled. It's also probably not all that realistic to plan on telling the monitor what size of batch we need for test tags. the best thing to do is probably change self.model.test_batch_size to match what the monitor does")

            assert Vv.shape[0] == Hv.shape[0]
            assert Hv.shape[1] == self.model.nhid


        mu = self.model.mu
        alpha = self.model.alpha
        W = self.model.W
        B = self.model.B
        w = self.model.w

        BW = B.dimshuffle(0,'x') * W

        HS = H * Mu1

        mean_term = mu * alpha

        data_term = T.dot(V, BW)

        iterm_part_1 = - T.dot(T.dot(HS, W.T), BW)
        iterm_part_2 = w * HS

        interaction_term = iterm_part_1 + iterm_part_2

        for i1v, Vv in get_debug_values(iterm_part_1, V):
            assert i1v.shape[0] == Vv.shape[0]

        debug_interm = mean_term + data_term
        numer = debug_interm + interaction_term

        alpha = self.model.alpha
        w = self.model.w

        denom = alpha + w

        Mu1 =  numer / denom

        return Mu1

    def var_s1_hat(self):
        """Returns the variational parameter for the variance of s given h=1
            This is data-independent so its just a vector of size (nhid,) and
            doesn't take any arguments """

        rval =  1./ (self.model.alpha + self.model.w )

        rval.name = 'var_s1'

        return rval

    def infer_H_hat(self, V, H_hat, S_hat):

        half = as_floatX(.5)
        alpha = self.model.alpha
        w = self.model.w
        mu = self.model.mu
        W = self.model.W
        B = self.model.B
        BW = B.dimshuffle(0,'x') * W

        HS = H_hat * S_hat

        t1f1t1 = V

        t1f1t2 = -T.dot(HS,W.T)
        iterm_corrective = w * H_hat *T.sqr(S_hat)

        t1f1t3_effect = - half * w * T.sqr(S_hat)

        term_1_factor_1 = t1f1t1 + t1f1t2

        term_1 = T.dot(term_1_factor_1, BW) * S_hat + iterm_corrective + t1f1t3_effect

        term_2_subterm_1 = - half * alpha * T.sqr(S_hat)

        term_2_subterm_2 = alpha * S_hat * mu

        term_2_subterm_3 = - half * alpha * T.sqr(mu)

        term_2 = term_2_subterm_1 + term_2_subterm_2 + term_2_subterm_3

        term_3 = self.model.bias_hid

        term_4 = -half * T.log(alpha + self.model.w)

        term_5 = half * T.log(alpha)

        arg_to_sigmoid = term_1 + term_2 + term_3 + term_4 + term_5

        H = T.nnet.sigmoid(arg_to_sigmoid)

        return H

    def damp(self, old, new, new_coeff):
        return new_coeff * new + (1. - new_coeff) * old

    def variational_inference(self, V, return_history = False):
        """

            return_history: if True:
                                returns a list of dictionaries with
                                showing the history of the variational
                                parameters
                                throughout fixed point updates
                            if False:
                                returns a dictionary containing the final
                                variational parameters
        """

        alpha = self.model.alpha


        var_s0_hat = 1. / alpha
        var_s1_hat = self.var_s1_hat()


        H   =    self.init_H_hat(V)
        Mu1 =    self.init_S_hat(V)

        def check_H(my_H, my_V):
            if my_H.dtype != config.floatX:
                raise AssertionError('my_H.dtype should be config.floatX, but they are '
                        ' %s and %s, respectively' % (my_H.dtype, config.floatX))

            allowed_v_types = ['float32']

            if config.floatX == 'float64':
                allowed_v_types.append('float64')

            assert my_V.dtype in allowed_v_types

            if config.compute_test_value != 'off':
                from theano.gof.op import PureOp
                Hv = PureOp._get_test_value(my_H)

                Vv = my_V.tag.test_value

                assert Hv.shape[0] == Vv.shape[0]

        check_H(H,V)

        def make_dict():

            return {
                    'H_hat' : H,
                    'S_hat' : Mu1,
                    'var_s0_hat' : var_s0_hat,
                    'var_s1_hat': var_s1_hat,
                    }

        history = [ make_dict() ]

        for new_H_coeff, new_S_coeff in zip(self.h_new_coeff_schedule, self.s_new_coeff_schedule):

            new_Mu1 = self.infer_S_hat(V, H, Mu1)

            if self.clip_reflections:
                clipped_Mu1 = reflection_clip(Mu1 = Mu1, new_Mu1 = new_Mu1, rho = self.rho)
            else:
                clipped_Mu1 = new_Mu1
            Mu1 = self.damp(old = Mu1, new = clipped_Mu1, new_coeff = new_S_coeff)
            new_H = self.infer_H_hat(V, H, Mu1)

            H = self.damp(old = H, new = new_H, new_coeff = new_H_coeff)

            check_H(H,V)

            history.append(make_dict())

        if return_history:
            return history
        else:
            return history[-1]

class Grad_M_Step:
    """ A partial M-step based on gradient ascent.
        More aggressive M-steps are possible but didn't work particularly well in practice
        on STL-10/CIFAR-10
    """

    def __init__(self, learning_rate, B_learning_rate_scale  = 1,
            W_learning_rate_scale = 1, p_penalty = 0.0, B_penalty = 0.0, alpha_penalty = 0.0):
        self.learning_rate = np.cast[config.floatX](float(learning_rate))

        self.B_learning_rate_scale = np.cast[config.floatX](float(B_learning_rate_scale))
        self.W_learning_rate_scale = np.cast[config.floatX](float(W_learning_rate_scale))
        self.p_penalty = as_floatX(p_penalty)
        self.B_penalty = as_floatX(B_penalty)
        self.alpha_penalty = as_floatX(alpha_penalty)

    def get_updates(self, model, stats, H_hat, S_hat):

        params = model.get_params()

        obj = model.expected_log_prob_vhs(stats, H_hat, S_hat) - T.mean(model.p) * self.p_penalty - T.mean(model.B)*self.B_penalty-T.mean(model.alpha)*self.alpha_penalty


        constants = set(stats.d.values()).union([H_hat, S_hat])

        grads = T.grad(obj, params, consider_constant = constants)

        updates = {}

        for param, grad in zip(params, grads):
            learning_rate = self.learning_rate

            if param is model.W:
                learning_rate = learning_rate * self.W_learning_rate_scale

            if param is model.B_driver:
                #can't use *= since this is a numpy ndarray now
                learning_rate = learning_rate * self.B_learning_rate_scale

            if param is model.W and model.constrain_W_norm:
                #project the gradient into the tangent space of the unit hypersphere
                #see "On Gradient Adaptation With Unit Norm Constraints"
                #this is the "true gradient" method on a sphere
                #it computes the gradient, projects the gradient into the tangent space of the sphere,
                #then moves a certain distance along a geodesic in that direction

                g_k = learning_rate * grad

                h_k = g_k -  (g_k*model.W).sum(axis=0) * model.W

                theta_k = T.sqrt(1e-8+T.sqr(h_k).sum(axis=0))

                u_k = h_k / theta_k

                updates[model.W] = T.cos(theta_k) * model.W + T.sin(theta_k) * u_k

            else:
                pparam = param

                inc = learning_rate * grad

                updated_param = pparam + inc

                updates[param] = updated_param

        return updates

    def needed_stats(self):
        return S3C.expected_log_prob_vhs_needed_stats()

    def get_monitoring_channels(self, V, model):

        hid_observations = model.e_step.variational_inference(V)

        stats = SufficientStatistics.from_observations(needed_stats = S3C.expected_log_prob_vhs_needed_stats(),
                V = V, **hid_observations)

        H_hat = hid_observations['H_hat']
        S_hat = hid_observations['S_hat']

        obj = model.expected_log_prob_vhs(stats, H_hat, S_hat)

        return { 'expected_log_prob_vhs' : obj }

