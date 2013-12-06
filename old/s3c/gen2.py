script1 = open('config/stl/extract/M_script_1.sh','w')
script2 = open('config/stl/extract/M_script_2.sh','w')
script3 = open('config/stl/extract/M_script_3.sh','w')

for i in xrange(10):
    start = i * 5000
    end = (i+1) * 5000

    char = chr(ord('A')+i)

    fname = 'config/stl/extract/M_train_'+char+'.yaml'

    if i < 3:
        script = script1
    elif i < 6:
        script = script2
    else:
        script = script3

    script.write('THEANO_FLAGS="device=gpu0" python extract_features.py '+fname+'\n')

    f = open(fname,'w')
    f.write("""!obj:galatea.s3c.extract_features.FeatureExtractor {
        "batch_size" : 1,
        "model_path" : "${GALATEA_PATH}/s3c/config/stl/M.pkl",         "pooling_region_counts": [3],
        "save_paths" : [ "${FEATURE_EXTRACTOR_YAML_PATH}.npy" ],
        "feature_type" : "exp_h",
        "dataset_name" : "stl10",
        "restrict" : [ %d,  %d ],
        "which_set" : "train"
}""" % (start, end))
    f.close()

script1.close()
script2.close()
script3.close()