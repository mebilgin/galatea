import numpy as np
from pylearn2.utils import serial
import sys
from dbm_inpaint import DBM_Inpaint_Binary
from dbm_inpaint import MaskGen
import theano.tensor as T
from theano import function
from pylearn2.config import yaml_parse
from pylearn2.gui.patch_viewer import PatchViewer

ignore, model_path = sys.argv
m = 10
model = serial.load(model_path)
model.set_batch_size(m)

try:
    mask_gen = model.mask_gen
    cost = model.cost
    cost.mask_gen = mask_gen
except:
    raise
    try:
        drop_prob = model.dbm_inpaint_drop_prob
        n_iter = model.dbm_inpaint_n_iter
        balance = model.dbm_inpaint_balance
    except:
        try:
            drop_prob = model.dbm_denoise_drop_prob
            n_iter = model.dbm_denoise_n_iter
            try:
                balance = model.dbm_denoise_balance
            except:
                balance = False
        except:
            drop_prob = 0.5
            n_iter = 5
            balance = False

    mask_gen = MaskGen(drop_prob = drop_prob, balance = balance)
    cost = DBM_Inpaint_Binary(mask_gen = mask_gen, n_iter = n_iter)
    cost.mask_gen = mask_gen

space = model.get_input_space()
X = space.make_theano_batch()

denoising = cost(model,X,return_locals=True)

drop_mask = denoising['drop_mask']
outputs = [ drop_mask ]
history = denoising['history']
for elem in history:
    try:
        outputs.append(elem['X_hat'])
    except:
        outputs.append(elem['V_hat'])

f = function([X],outputs)


if model.dataset_yaml_src.find('train') != -1:
    print 'test hack'
    model.dataset_yaml_src = model.dataset_yaml_src.replace('train','test')

dataset = yaml_parse.load(model.dataset_yaml_src)


if X.ndim == 2:
    X = dataset.get_batch_design(m)
else:
    X = dataset.get_batch_topo(m)

outputs = f(X)
drop_mask = outputs[0]
print 'empirical drop prob:',drop_mask.mean()
X_sequence = outputs[1:]


if X.ndim == 2:
    Xt, drop_mask = [ dataset.get_topological_view(mat)
        for mat in [X, drop_mask] ]
else:
    Xt = X

rows = m
mapback = hasattr(dataset, 'mapback_for_viewer')

cols = 2+len(X_sequence)
if mapback:
    rows = 2 * m
    M = dataset.get_topological_view(dataset.mapback_for_viewer(X))
    M_sequence = [ dataset.get_topological_view(dataset.mapback_for_viewer(mat)) for mat in X_sequence ]
X = dataset.adjust_for_viewer(Xt)
if X_sequence[0].ndim == 2:
    X_sequence = [ dataset.get_topological_view(mat) for mat in X_sequence ]
X_sequence = [ dataset.adjust_for_viewer(mat) for mat in X_sequence ]


pv = PatchViewer( (rows, cols), (X.shape[1], X.shape[2]), is_color = True)

for i in xrange(m):

    #add original patch
    patch = X[i,:,:,:]
    if patch.shape[-1] != 3:
        patch = np.concatenate( (patch,patch,patch), axis=2)
    pv.add_patch(patch, rescale = False)

    #mark the masked areas as red
    mask_patch = drop_mask[i,:,:,0]
    if drop_mask.shape[-1] > 1 and mask_gen.n_channels > 1:
        assert np.all(mask_patch == drop_mask[i,:,:,1])
        assert np.all(mask_patch == drop_mask[i,:,:,2])
    red_channel = patch[:,:,0]
    green_channel = patch[:,:,1]
    blue_channel = patch[:,:,2]
    red_channel[mask_patch == 1] = 1.
    green_channel[mask_patch == 1] = -1.
    blue_channel[mask_patch == 1] = -1.
    if drop_mask.shape[-1] > 1 and mask_gen.n_channels == 1:
        mask_patch = drop_mask[i,:,:,1]
        red_channel[mask_patch == 1] = -1
        green_channel[mask_patch == 1] = 1
        blue_channel[mask_patch == 1] = -1
        mask_patch = drop_mask[i,:,:,2]
        red_channel[mask_patch == 1] = -1
        green_channel[mask_patch == 1] = -1
        blue_channel[mask_patch == 1] = 1

    patch[:,:,0] = red_channel
    patch[:,:,1] = green_channel
    patch[:,:,2] = blue_channel
    pv.add_patch(patch, rescale = False)

    #add filled-in patch
    for j in xrange(len(X_sequence)):
        patch = X_sequence[j][i,:,:,:]
        if patch.shape[-1] != 3:
            patch = np.concatenate( (patch,patch,patch), axis=2)
        pv.add_patch(patch, rescale = False)

    if mapback:
        patch = M[i,:,:,:]
        if patch.shape[-1] != 3:
            patch = np.concatenate( (patch,patch,patch),axis=2)
        pv.add_patch(patch, rescale = False)

        #dummy placeholder, we can't actually visualize the masking in original space
        pv.add_patch(patch * 0, rescale = False)

        #add filled-in patch
        for j in xrange(len(M_sequence)):
            patch = M_sequence[j][i,:,:,:]
            if patch.shape[-1] != 3:
                patch = np.concatenate( (patch,patch,patch), axis=2)
            pv.add_patch(patch, rescale = False)

pv.show()
