#!/bin/bash

################################################################################
# RUN FREESURFER ON BIDS DATA
#
# This step must be run before the data can be fully processed through fMRIPrep
#
# The fMRIPrep singularity was installed using the following code:
# 	SINGULARITY_TMPDIR=/EBC/processing SINGULARITY_CACHEDIR=/EBC/processing singularity build /EBC/processing/singularity_images/fmriprep-24.0.0.simg docker://nipreps/fmriprep:24.0.0
#
################################################################################

# usage documentation - shown if no text file is provided or if script is run outside EBC directory
Usage() {
	echo
	echo
	echo "Usage:"
	echo "./run_freesurfer.sh <list of subjects>"
	echo
	echo "Example:"
	echo "./run_freesurfer.sh TEBC-5y_subjs.txt"
	echo
	echo "TEBC-5y_subjs.txt is a file containing the participants to run Freesurfer on:"
	echo "001"
	echo "002"
	echo "..."
	echo
	echo
	echo "This script must be run within the /EBC/ directory on the server due to space requirements."
	echo "The script will terminiate if run outside of the /EBC/ directory."
	echo
	echo "Script created by Manuel Blesa & Melissa Thye"
	echo
	exit
}
[ "$1" = "" ] && Usage

# if the script is run outside of the EBC directory (e.g., in home directory where space is limited), terminate the script and show usage documentation
if [[ ! "$PWD" =~ "/EBC/" ]]
then Usage
fi

# define directories
projDir=`cat ../../PATHS.txt`
singularityDir="${projDir}/singularity_images"

# convert the singularity image to a sandbox if it doesn't already exist to avoid having to rebuild on each run
if [ ! -d ${singularityDir}/fmriprep_sandbox ]
then
	apptainer build --sandbox ${singularityDir}/fmriprep_sandbox ${singularityDir}/fmriprep-24.0.0.simg
fi

# define subjects from text document
subjs=$(cat $1 | awk '{print $1}') 

# extract sample from list of subjects filename (i.e., are these pilot or HV subjs)
sample=` basename $1 | cut -d '-' -f 3 | cut -d '.' -f 1 `
cohort=` basename $1 | cut -d '_' -f 1 `

# define data directories depending on sample information
if [[ ${sample} == 'pilot' ]]
then
	bidsDir="/EBC/preprocessedData/${cohort}/BIDs_data/pilot"
	derivDir="/EBC/preprocessedData/${cohort}/derivatives/pilot"
elif [[ ${sample} == 'HV' ]]
then
	bidsDir="/EBC/preprocessedData/${cohort}-adultpilot/BIDs_data"
	derivDir="/EBC/preprocessedData/${cohort}-adultpilot/derivatives"
else
	bidsDir="/EBC/preprocessedData/${cohort}/BIDs_data"
	derivDir="/EBC/preprocessedData/${cohort}/derivatives"
fi

# create derivatives directory if it doesn't exist
if [ ! -d ${derivDir} ]
then 
	mkdir -p ${derivDir}
fi

# export freesurfer license file location
export license=/EBC/local/infantFS/freesurfer/license.txt

# change the location of the singularity cache ($HOME/.singularity/cache by default, but limited space in this directory)
export APPTAINER_TMPDIR=${singularityDir}
export APPTAINER_CACHEDIR=${singularityDir}
unset PYTHONPATH

# prepare some writeable bind-mount points
export APPTAINERENV_TEMPLATEFLOW_HOME=${singularityDir}/fmriprep/.cache/templateflow

# display subjects
echo
echo "Running Freesurfer via fMRIPrep for..."
echo "${subjs}"

# iterate for all subjects in the text file
while read p
do	
	NAME=` basename ${p} | awk -F- '{print $NF}' `	# subj number
	
	# check whether the file already exists
	if [ ! -f ${derivDir}/sourcedata/freesurfer/sub-${NAME}/mri/aparc+aseg.mgz ]
	then

		echo
		echo "Running anatomical workflow contained in fMRIprep for sub-${NAME}"
		echo
		
		# make output subject derivatives directory
		mkdir -p ${derivDir}/sub-${NAME}

		# run singularity
		apptainer run -C -B /EBC:/EBC,${singularityDir}:/opt/templateflow \
		${singularityDir}/fmriprep_sandbox  								\
		${bidsDir} ${derivDir}												\
		participant															\
		--participant-label ${NAME}											\
		--skip_bids_validation												\
		--nthreads 16														\
		--omp-nthreads 16													\
		--anat-only															\
		--output-space MNI152NLin2009cAsym:res-2 T1w						\
		--derivatives ${derivDir}											\
		--stop-on-first-crash												\
		-w ${singularityDir}												\
		--fs-license-file ${license}  > ${derivDir}/sub-${NAME}/log_freesurfer_sub-${NAME}.txt
		
		# move subject report and freesurfer output files to appropriate directories
		mv ${derivDir}/*dseg.tsv ${derivDir}/sourcedata/freesurfer
		mv ${derivDir}/sub-${NAME}.html ${derivDir}/sub-${NAME}
			
		# give other users permissions to created files
		chmod -R a+wrx ${derivDir}/sub-${NAME}

	fi

done <$1
