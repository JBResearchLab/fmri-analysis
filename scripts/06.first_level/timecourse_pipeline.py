"""
Individual run analysis using outputs from fMRIPrep

Adapted script from original notebook:
https://github.com/poldrack/fmri-analysis-vm/blob/master/analysis/postFMRIPREPmodelling/First%20and%20Second%20Level%20Modeling%20(FSL).ipynb

More information on what this script is doing - beyond the commented code - is provided on the lab's github wiki page

Requirement: BIDS dataset (including events.tsv), derivatives directory with fMRIPrep outputs, and modeling files

"""
from nipype.interfaces import fsl
from nipype import Workflow, Node, IdentityInterface, Function, DataSink, JoinNode, MapNode
import nilearn
import os
import os.path as op
import numpy as np
import argparse
from bids.layout import BIDSLayout
from niflow.nipype1.workflows.fmri.fsl import create_susan_smooth
import pandas as pd
import glob
import shutil
from datetime import datetime


# define first level workflow function
def create_resting_workflow(projDir, derivDir, workDir, outDir, 
                            sub, task, ses, runs, regressor_opts, 
                            smoothing_kernel_size, smoothDir, hpf, filter_opt, TR, detrend, standardize, dropvols,
                            name='sub-{}_task-{}_timecourses'):
    """Processing pipeline"""
    
    # initialize workflow
    wf = Workflow(name=name.format(sub, task),
                  base_dir=workDir)
    
    # configure workflow
    # parameterize_dirs: parameterizations over 32 characters will be replaced by their hash; essentially prevented an issue of having too many characters in a file/folder name (i.e., https://github.com/nipy/nipype/issues/2061#issuecomment-1189562017)
    wf.config['execution']['parameterize_dirs'] = False

    infosource = Node(IdentityInterface(fields=['run_id']), name='infosource')
    
    # define iterables to run nodes over runs
    infosource.iterables = [('run_id', runs)]
    infosource.synchronize = True
   
    # enable/disable smoothing based on value provided in config file
    if smoothing_kernel_size != 0: # if smoothing kernel size is not 0
        # use spatial smoothing
        run_smoothing = True
        print('Spatial smoothing will be run using a {}mm smoothing kernel unless prior outputs are found.'.format(smoothing_kernel_size))
    else: 
        # don't do spatial smoothing
        run_smoothing = False
        print('Spatial smoothing will not be run.')
        
    # define data grabber function
    def data_grabber(sub, task, derivDir, smoothDir, ses, run_id, outDir):
        """Quick filegrabber ala SelectFiles/DataGrabber"""
        import os
        import os.path as op
        import shutil
        
        # define output filename and path, depending on whether session information is in directory/file names
        if ses != 'no': # if session was provided
            # define path to preprocessed functional and mask data (subject derivatives func folder)
            prefix = 'sub-{}_ses-{}_task-{}_run-{:02d}'.format(sub, ses, task, run_id)
            funcDir = op.join(derivDir, 'sub-{}'.format(sub), 'ses-{}'.format(ses), 'func')
            mni_mask = op.join(funcDir, 'sub-{}_ses-{}_space-MNI152NLin2009cAsym_res-2_desc-brain_mask_allruns-BOLDmask.nii.gz'.format(sub, ses))
            
        else: # if session was 'no'
            # define path to preprocessed functional and mask data (subject derivatives func folder)
            prefix = 'sub-{}_task-{}_run-{:02d}'.format(sub, task, run_id)
            funcDir = op.join(derivDir, 'sub-{}'.format(sub), 'func')
            mni_mask = op.join(funcDir, 'sub-{}_space-MNI152NLin2009cAsym_res-2_desc-brain_mask_allruns-BOLDmask.nii.gz'.format(sub))

        # grab the confound and MNI file
        confound_file = op.join(funcDir, '{}_desc-confounds_timeseries.tsv'.format(prefix))
        mni_file = op.join(funcDir, '{}_space-MNI152NLin2009cAsym_res-2_desc-preproc_bold.nii.gz'.format(prefix))
        
        # grab the outlier file generated by rapidart
        art_file = op.join(funcDir, 'art', '{}{:02d}'.format(task, run_id), 'art.{}_space-MNI152NLin2009cAsym_res-2_desc-preproc_bold_outliers.txt'.format(prefix))
        
        # check to see whether outputs exist in smoothDir (if resultsDir was specified in config file)
        if smoothDir:
            smooth_file = op.join(smoothDir, 'sub-{}'.format(sub), 'preproc', 'run{}'.format(run_id), '{}_space-MNI-preproc_bold_smooth.nii.gz'.format(prefix))
            if os.path.exists(smooth_file):
                print('Previously smoothed data file has been found and will be used: {}'.format(mni_file))
                mni_file = smooth_file
            else:
                print('WARNING: A resultsDir was specified in the config file but no smoothed data files were found.')
        else:
            print('No resultsDir specified in the config file. Using fMRIPrep outputs')
        
        # save mni_file
        preprocDir = op.join(outDir, 'preproc', 'run{}'.format(run_id))
        os.makedirs(preprocDir, exist_ok=True)
        shutil.copy(mni_file, preprocDir)  

        return confound_file, art_file, mni_file, mni_mask
        
    
    datasource = Node(Function(output_names=['confound_file',
                                             'art_file',
                                             'mni_file',
                                             'mni_mask'],
                               function=data_grabber),
                               name='datasource')
    datasource.inputs.sub = sub
    datasource.inputs.task = task
    datasource.inputs.derivDir = derivDir
    datasource.inputs.outDir = outDir
    datasource.inputs.smoothDir = smoothDir
    datasource.inputs.ses = ses
    wf.connect(infosource, 'run_id', datasource, 'run_id')

    # if requested, smooth before running model
    if run_smoothing:
        # create_susan_smooth refers to FSL's Susan algorithm for smoothing data
        smooth = create_susan_smooth()
        
        # smoothing workflow requires the following inputs:
            # inputnode.in_files : functional runs (filename or list of filenames)
            # inputnode.fwhm : fwhm for smoothing with SUSAN
            # inputnode.mask_file : mask used for estimating SUSAN thresholds (but not for smoothing)
        
        # provide smoothing_kernel_size, mask files, and split mni file
        smooth.inputs.inputnode.fwhm = smoothing_kernel_size
        wf.connect(datasource, 'mni_mask', smooth, 'inputnode.mask_file')
        wf.connect(datasource, 'mni_file', smooth, 'inputnode.in_files')

    # if drop volumes requested (likely always no for us)
    if dropvols != 0:
        roi = Node(fsl.ExtractROI(t_min=dropvols, t_size=-1), name='extractroi')
        # drop volumes from smoothed data if smoothing was requested
        if run_smoothing:
            wf.connect(smooth, 'outputnode.smoothed_files', roi, 'in_file')
        # drop volumes from unsmoothed data if smoothing was not requested
        else: 
            wf.connect(datasource, 'mni_file', roi, 'in_file')
    
    # create a dictionary for mapping between config file and labels used in confounds file (more options can be added later)
    regressor_dict = {'FD': 'framewise_displacement',
                      'DVARS':'std_dvars',
                      'aCompCor': ['a_comp_cor_00', 'a_comp_cor_01', 'a_comp_cor_02', 'a_comp_cor_03', 'a_comp_cor_04']}
    
    # extract the entries from the dictionary that match the key value provided in the config file
    regressor_list=list({r: regressor_dict[r] for r in regressor_opts if r in regressor_dict}.values())
    
    # remove nested lists if present (e.g., aCompCor regressors)
    regressor_names=[]
    for element in regressor_list:
        if type(element) is list:
            for item in element:
                regressor_names.append(item)
        else:
            regressor_names.append(element)
    
    print('Using the following nuisance regressors in the model: {}'.format(regressor_names))
    
    # generate motion regressors using fmriprep confounds and rapidart outputs, if requested
    # the output is a numpy array containing motion related regressors as its columns
    def create_motion_reg(sub, confound_file, art_file, regressor_opts, regressor_names, dropvols, run_id, outDir):
        import os
        import os.path as op        
        import pandas as pd
        from pandas.errors import EmptyDataError
        import numpy as np
        
        # read in confound file
        confounds = pd.read_csv(confound_file, sep='\t', na_values='n/a')
        
        # read in rapidart outlier file
        try:
            outliers = pd.read_csv(art_file, header=None)[0].astype(int)
        except EmptyDataError: # generate empty dataframe if no outlier volumes (i.e., empty text file)
            outliers = pd.DataFrame()
        
        # for each regressor
        regressors = []            
        for regressor in regressor_names:
            if regressor == 'framewise_displacement':
                print('Processing {} regressor'.format(regressor))
                regressors.append(confounds[regressor].fillna(0).iloc[dropvols:])
            elif regressor == 'std_dvars':
                print('Processing {} regressor'.format(regressor))
                regressors.append(confounds[regressor].fillna(0).iloc[dropvols:])
            else:
                regressors.append(confounds[regressor].iloc[dropvols:])
        
        # convert motion regressors to dataframe
        motion_params = pd.DataFrame(regressors).transpose()
        
        # generate vector of volume indices (where inclusion means to retain volume) to use for scrubbing
        vol_indx = np.arange(motion_params.shape[0], dtype=np.int64)
        
        # if art regressor was included in regressor_opts list in config file        
        if 'art' in regressor_opts:
            print('ART identified motion spikes will be scrubbed from data')
            if np.shape(outliers)[0] != 0: # if there are outlier volumes
                # remove excluded volumes from vec
                vol_indx = np.delete(vol_indx, [outliers])
                print('{} outlier volumes will be scrubbed in run-{:02d}'.format(len(outliers), run_id))
        
        # save nuisance regressor array as text file in subject output directory
        regDir = op.join(outDir, 'regressors')
        os.makedirs(regDir, exist_ok=True)
        motion_file = op.join(regDir, 'sub-{}_run-{:02d}_confounds.txt'.format(sub, run_id))
        scrub_file = op.join(regDir, 'sub-{}_run-{:02d}_retained_volumes.txt'.format(sub, run_id))
        pd.DataFrame(motion_params).to_csv(motion_file, index=False, header=False, sep ='\t')
        pd.DataFrame(vol_indx).to_csv(scrub_file, index=False, header=False, sep ='\t')
        
        return motion_params, vol_indx, outliers
    
    # define motion regressor node   
    regressorinfo = Node(Function(output_names=['motion_params',
                                                'vol_indx',
                                                'outliers'], 
                                  function=create_motion_reg), 
                                  name='regressorinfo')
    
    # from datasource and infosource nodes add confound and art files and run_id
    wf.connect(datasource, 'confound_file', regressorinfo, 'confound_file')
    wf.connect(datasource, 'art_file', regressorinfo, 'art_file')
    wf.connect(infosource, 'run_id', regressorinfo, 'run_id')
    # pass regressor option information to regressor info node
    regressorinfo.inputs.sub = sub
    regressorinfo.inputs.regressor_names = regressor_names
    regressorinfo.inputs.dropvols = dropvols
    regressorinfo.inputs.regressor_opts = regressor_opts
    regressorinfo.inputs.outDir = outDir
    
    # define function to denoise data
    def denoise_data(imgs, mni_mask, motion_params, vol_indx, outliers, TR, hpf, filter_opt, detrend, standardize, outDir, sub, run_id):
        import nibabel as nib
        from nibabel import load
        import nilearn
        from nilearn import image
        import pandas as pd
        import numpy as np
        import os
        import os.path as op
        
        # make output directory
        denoiseDir = op.join(outDir, 'denoised', 'run{}'.format(run_id))
        os.makedirs(denoiseDir, exist_ok=True)
        
        # the smoothing node returns a list object but clean_img needs a path to the file
        if isinstance(imgs, list):
            imgs=imgs[0]        
       
        
        # process options from config file
        if detrend == 'yes':
            detrend_opt = True
        else:
            detrend_opt = False
        if standardize != 'no':
            standardize_opt = standardize
        else:
            standardize_opt = False
            
        # convert filter from seconds to Hz
        hpf_hz = 1/hpf
        
        print('Will apply a {} filter using a high pass filter cutoff of {}Hz for run-{}.'.format(filter_opt, hpf_hz, run_id))

        # define kwargs input to signal.clean function
        if filter_opt == 'butterworth':
            kwargs_opts={'clean__sample_mask':vol_indx, 
                         'clean__butterworth__t_r':TR,
                         'clean__butterworth__high_pass':hpf_hz}
        elif filter_opt == 'cosine':
            kwargs_opts={'clean__sample_mask':vol_indx, 
                         'clean__cosine__t_r':TR,
                         'clean__cosine__high_pass':hpf_hz}
        else:
            kwargs_opts={'clean__sample_mask':vol_indx}

        # process signal data with parameters specified in config file
        denoised_data = image.clean_img(imgs, mask_img=mni_mask, confounds=motion_params, detrend=detrend_opt, standardize=standardize_opt, **kwargs_opts)

        # save denoised data
        denoise_file = op.join(denoiseDir, 'sub-{}_run-{:02d}_denoised_bold.nii.gz'.format(sub, run_id))
        nib.save(denoised_data, denoise_file)
        
        # load denoised data and extract dimension info
        denoise_img = image.load_img(denoised_data)
        img_dim = denoise_img.shape
        
        # load input data and extract volume info
        input_img = image.load_img(imgs)
        nVols = input_img.shape[3]
        
        # create vector of volumes for indexing
        all_vols_vec = np.arange(nVols, dtype=np.int64)
        
        # create nan volume
        nan_vol = np.empty((img_dim[:-1])) # 97,115,97, 1
        nan_vol[:] = np.nan
        nan_img = image.new_img_like(denoise_img, nan_vol, affine=denoise_img.affine)

        # pad denoised data with nan vols where vols were scrubbed
        d=0 # index for denoised data which has a volume for index in vol_indx [nVols - outliers]
        pad_imgs = list()
        for vol in all_vols_vec: # for each volume
            if vol in vol_indx:
                tmp_vol = image.index_img(denoise_img, d)
                pad_imgs.append(tmp_vol)
                d += 1
            else:
                tmp_vol = nan_img
                pad_imgs.append(tmp_vol)
        
        # concatente list of 3D imgs to one 4D img
        pad_concat = image.concat_imgs(pad_imgs)
        
        # save padded data
        pad_file = op.join(denoiseDir, 'sub-{}_run-{:02d}_denoised_padded_bold.nii.gz'.format(sub, run_id))
        nib.save(pad_concat, pad_file)
        
        return denoised_data
    
    # process signal, passing generated confounds
    cleansignal = Node(Function(function=denoise_data), 
                                name='cleansignal')
    wf.connect(datasource, 'mni_mask', cleansignal, 'mni_mask')
    wf.connect(regressorinfo, 'motion_params', cleansignal, 'motion_params')
    wf.connect(regressorinfo, 'vol_indx', cleansignal, 'vol_indx')
    wf.connect(regressorinfo, 'outliers', cleansignal, 'outliers')
    wf.connect(infosource, 'run_id', cleansignal, 'run_id')
    cleansignal.inputs.sub = sub
    cleansignal.inputs.TR = TR
    cleansignal.inputs.hpf = hpf
    cleansignal.inputs.detrend = detrend
    cleansignal.inputs.standardize = standardize
    cleansignal.inputs.outDir = outDir
    cleansignal.inputs.filter_opt = filter_opt
    
    # pass data to cleansignal depending on whether dropvols and/or smoothing were requested
    if dropvols !=0: # if drop volumes requested (likely always no for us)
        # pass dropped value files (smoothed or not depending on logic above) as input to cleansignal
        wf.connect(roi, 'roi_file', cleansignal, 'imgs')
    else:
        if run_smoothing:
            # pass smoothed output files as functional runs to modelspec
            wf.connect(smooth, 'outputnode.smoothed_files', cleansignal, 'imgs')
        else: 
           # pass unsmoothed output files as functional runs to modelspec
            wf.connect(datasource, 'mni_file', cleansignal, 'imgs')

    # extract components from working directory cache and store it at a different location
    sinker = Node(DataSink(), name='datasink')
    sinker.inputs.base_directory = outDir
    sinker.inputs.regexp_substitutions = [('_run_id_', 'run'),
                                          ('_smooth0/','')]
    
    # define where output files are saved
    if run_smoothing:
        wf.connect(smooth, 'outputnode.smoothed_files', sinker, 'preproc.@')
    
    return wf
    
# define function to extract subject-level data for workflow
def process_subject(layout, projDir, derivDir, outDir, workDir, 
                    sub, task, ses, sub_runs, regressor_opts, 
                    smoothing_kernel_size, smoothDir, hpf, filter_opt, detrend, standardize, dropvols):
    """Grab information and start nipype workflow
    We want to parallelize runs for greater efficiency
    """
    # identify scan and events files
    if ses != 'no': # if session was provided
        print('Session information provided. Assuming data are organized into session folders.')
        
        # identify scans file (from derivDir bc artifact information is saved in the processed scans.tsv file)
        scans_tsv = glob.glob(op.join(derivDir, 'sub-{}'.format(sub), 'ses-{}'.format(ses), 'func', '*_scans.tsv'))[0]
        
    else: # if session was 'no'
        # identify scans file (from derivDir bc artifact information is saved in the processed scans.tsv file)
        scans_tsv = glob.glob(op.join(derivDir, 'sub-{}'.format(sub), 'func', '*_scans.tsv'))[0]
        
    # return error if scan file not found
    if not os.path.isfile(scans_tsv):
        raise IOError('scans file {} not found.'.format(scans_tsv))

    # read in scans file
    scans_df = pd.read_csv(scans_tsv, sep='\t')

    # extract subject, task, and run information from filenames in scans.tsv file
    scans_df['task'] = scans_df['filename'].str.split('task-', expand=True).loc[:,1]
    scans_df['task'] = scans_df['task'].str.split('_run', expand=True).loc[:,0]
    scans_df['task'] = scans_df['task'].str.split('_bold', expand=True).loc[:,0]
    scans_df['run'] = scans_df['filename'].str.split(scans_df['task'][0], expand=True).loc[:,1]
    scans_df['run'] = scans_df['run'].str.split('_bold', expand=True).loc[:,0]
    if not scans_df['run'][0]: # if no run information
        scans_df['run'] = None
    else:
        scans_df['run'] = scans_df['run'].str.split('-', expand=True).loc[:,1]
    
    # remove runs tagged with excessive motion, that are for a different task, or aren't in run list in the config file
    keepruns = scans_df[(scans_df.MotionExclusion == False) & (scans_df.task == task) & (scans_df.run.isin(['{:02d}'.format(r) for r in sub_runs]))].run
    
    # convert runs to list of values
    keepruns = list(keepruns.astype(int).values)

    # if the participant didn't have any runs for this task or all runs were excluded due to motion
    if not keepruns:
        raise FileNotFoundError('No included bold {} runs found for sub-{}'.format(task, sub))
   
    # extract TR info from bidsDir bold json files (assumes TR is same across runs)
    epi = layout.get(subject=sub, suffix='bold', task=task, return_type='file')[0] # take first file
    TR = layout.get_metadata(epi)['RepetitionTime'] # extract TR field
    
    # define subject output directory
    suboutDir = op.join(outDir, 'sub-{}'.format(sub))

    # call resting state workflow with extracted subject-level data
    wf = create_resting_workflow(projDir, derivDir, workDir, suboutDir, 
                                 sub, task, ses, keepruns, regressor_opts, 
                                 smoothing_kernel_size, smoothDir, hpf, filter_opt, TR, detrend, standardize, dropvols)                                    
    return wf

# define command line parser function
def argparser():
    # create an instance of ArgumentParser
    parser = argparse.ArgumentParser()
    # attach argument specifications to the parser
    parser.add_argument('-p', dest='projDir',
                        help='Project directory')
    parser.add_argument('-w', dest='workDir', default=os.getcwd(),
                        help='Working directory')
    parser.add_argument('-o', dest='outDir', default=os.getcwd(),
                        help='Output directory')
    parser.add_argument('-s', dest='subjects', nargs='*',
                        help='List of subjects to process (default: all)')
    parser.add_argument('-r', dest='runs', nargs='*',
                        help='List of runs for each subject')    
    parser.add_argument('-c', dest='config',
                        help='Configuration file')                                            
    parser.add_argument('-sparse', action='store_true',
                        help='Specify a sparse model')
    parser.add_argument('-m', dest='plugin',
                        help='Nipype plugin to use (default: MultiProc)')
    return parser

# define main function that parses the config file and runs the functions defined above
def main(argv=None):
    # call argparser function that defines command line inputs
    parser = argparser()
    args = parser.parse_args(argv)   
        
    # print if the project directory is not found
    if not op.exists(args.projDir):
        raise IOError('Project directory {} not found.'.format(args.projDir))
    
    # print if config file is not found
    if not op.exists(args.config):
        raise IOError('Configuration file {} not found. Make sure it is saved in your project directory!'.format(args.config))
    
    # define output and working directories
    workDir, outDir = op.realpath(args.workDir), op.realpath(args.outDir)
    
    # identify analysis README file
    readme_file=op.join(outDir, 'README.txt')
    
    # read in configuration file and parse inputs
    config_file=pd.read_csv(args.config, sep='\t', header=None, index_col=0).replace({np.nan: None})
    bidsDir=config_file.loc['bidsDir',1]
    derivDir=config_file.loc['derivDir',1]
    smoothDir=config_file.loc['resultsDir',1]
    task=config_file.loc['task',1]
    ses=config_file.loc['sessions',1]
    smoothing_kernel_size=int(config_file.loc['smoothing',1])
    hpf=int(config_file.loc['hpf',1])
    filter_opt=config_file.loc['filter',1]
    detrend=config_file.loc['detrend',1]
    standardize=config_file.loc['standardize',1]
    dropvols=int(config_file.loc['dropvols',1])
    regressor_opts=config_file.loc['regressors',1].replace(' ','').split(',')
    overwrite=config_file.loc['overwrite',1]
    
    # if user requested overwrite, delete previous directories
    if (overwrite == 'yes') & (len(os.listdir(workDir)) != 0):
        print('Overwriting existing outputs.')
        shutil.copy(readme_file, args.projDir)  # temporarily copy README to project directory
        # remove directories
        shutil.rmtree(outDir)
        # create new directories
        os.mkdir(outDir)
        os.mkdir(workDir)
        tmp_file=op.join(args.projDir, 'README.txt')
        shutil.copy(tmp_file, readme_file) # copy README to new working directory
        os.remove(tmp_file) # delete temp file
    
    # if user requested no overwrite, create new working directory with date and time stamp
    if (overwrite == 'no') & (len(os.listdir(workDir)) != 0):
        print('Creating new output directories to avoid overwriting existing outputs.')
        today = datetime.now() # get date
        datestring = today.strftime('%Y-%m-%d_%H-%M-%S')
        outDir = (outDir + '_' + datestring) # new directory path
        workDir = op.join(outDir, 'processing')
        # create new directories
        os.mkdir(outDir)
        os.mkdir(workDir)      
        shutil.copy(readme_file, outDir)  # copy README to new output directory
        readme_file=op.join(outDir, 'README.txt') # re-identify current analysis README file
    
    # print if BIDS directory is not found
    if not op.exists(bidsDir):
        raise IOError('BIDS directory {} not found.'.format(bidsDir))
    
    # print if the fMRIPrep directory is not found
    if not op.exists(derivDir):
        raise IOError('Derivatives directory {} not found.'.format(derivDir))
    
    # add config details to project README file
    with open(args.config, 'r') as file_1, open(readme_file, 'a') as file_2:
        for line in file_1:
            file_2.write(line)
    
    # get layout of BIDS directory
    # this is necessary because the pipeline reads the functional json files that have TR info
    # the derivDir (where fMRIPrep outputs are) doesn't have json files with this information, so getting the layout of that directory will result in an error
    layout = BIDSLayout(bidsDir)

    # define subjects - if none are provided in the script call, they are extracted from the BIDS directory layout information
    subjects = args.subjects if args.subjects else layout.get_subjects()

    # for each subject in the list of subjects
    for index, sub in enumerate(subjects):
        # pass runs for this sub
        sub_runs=args.runs[index]
        sub_runs=sub_runs.replace(' ','').split(',') # split runs by separators
        sub_runs=list(map(int, sub_runs)) # convert to integers
              
        # create a process_subject workflow with the inputs defined above
        wf = process_subject(layout, args.projDir, derivDir, outDir, workDir, 
                             sub, task, ses, sub_runs, regressor_opts, 
                             smoothing_kernel_size, smoothDir, hpf, filter_opt, detrend, standardize, dropvols)
   
        # configure workflow options
        wf.config['execution'] = {'crashfile_format': 'txt',
                                  'remove_unnecessary_outputs': False,
                                  'keep_inputs': True}

        # run multiproc unless plugin specified in script call
        plugin = args.plugin if args.plugin else 'MultiProc'
        args_dict = {'n_procs' : 4}
        wf.run(plugin=plugin, plugin_args = args_dict)

# execute code when file is run as script (the conditional statement is TRUE when script is run in python)
if __name__ == '__main__':
    main()