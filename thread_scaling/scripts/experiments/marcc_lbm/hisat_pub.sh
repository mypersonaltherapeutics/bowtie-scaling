#!/bin/bash

THREAD_SERIES="1,4,8,12,16,20,24,28,32,36,40,44,48,56,60,68,76,84,92,96,100,104,108"

module load git
export LD_LIBRARY_PATH=/home-1/cwilks3@jhu.edu/tbb2017_20161128oss.bin/lib/intel64/gcc4.1:$LD_LIBRARY_PATH
export LIBRARY_PATH=/home-1/cwilks3@jhu.edu/tbb2017_20161128oss.bin/lib/intel64/gcc4.1:$LIBRARY_PATH
export CPATH=/home-1/cwilks3@jhu.edu/tbb2017_20161128oss.bin/include:$CPATH
export LIBS="-lpthread -ltbb -ltbbmalloc -ltbbmalloc_proxy"

export INDEX_ROOT=/storage/indexes

export BT2_INDEX=$INDEX_ROOT
export HISAT_INDEX=$INDEX_ROOT

export ROOT1=/home-1/cwilks3@jhu.edu/scratch
export ROOT2=/local
rsync -av $ROOT1/ERR050082_1.fastq.shuffled2_extended.fq.block  $ROOT2/
rsync -av $ROOT1/ERR050082_2.fastq.shuffled2.fq.block $ROOT2/

export HISAT_READS=$ROOT2/ERR050082_1.fastq.shuffled2_extended.fq.block
export HISAT_READS_1=$ROOT2/ERR050082_1.fastq.shuffled2_extended.fq.block
export HISAT_READS_2=$ROOT2/ERR050082_2.fastq.shuffled2.fq.block

CONFIG=./experiments/hisat_pub.tsv
CONFIG_MP=./experiments/hisat_pub_mp.tsv

if [ ! -d "${1}/mp_mt_hisat" ]; then
	mkdir -p ${1}/mp_mt_hisat
fi

./experiments/marcc_lbm/run_mp_mt_hisat.sh ${1}/mp_mt_hisat > run_mp_mt_hisat.run 2>&1

#single
python ./master.py --reads-per-thread 330016 --index $BT2_INDEX/hg19 --hisat-index $HISAT_INDEX/hg19_hisat --hisat-U $HISAT_READS --hisat-m1 $HISAT_READS_1 --hisat-m2 $HISAT_READS_2 --sensitivities s --sam-dev-null --tempdir $ROOT2 --output-dir ${1} --nthread-series $THREAD_SERIES --config ${CONFIG} --multiply-reads 32 --reads-per-batch 32 --paired-mode 2 --no-no-io-reads --reads-count 125531901

#single MP
python ./master.py --multiprocess 330016 --index $BT2_INDEX/hg19 --hisat-index $HISAT_INDEX/hg19_hisat --hisat-U $HISAT_READS --hisat-m1 $HISAT_READS_1 --hisat-m2 $HISAT_READS_2 --sensitivities s --sam-dev-null --tempdir $ROOT2 --output-dir ${1} --nthread-series $THREAD_SERIES --config ${CONFIG_MP} --multiply-reads 32 --reads-per-batch 32 --paired-mode 2 --no-no-io-reads --reads-count 125531901

#paired
python ./master.py --reads-per-thread 320000 --index $BT2_INDEX/hg19 --hisat-index $HISAT_INDEX/hg19_hisat --hisat-U $HISAT_READS --hisat-m1 $HISAT_READS_1 --hisat-m2 $HISAT_READS_2 --sensitivities s --sam-dev-null --tempdir $ROOT2 --output-dir ${1} --nthread-series $THREAD_SERIES --config ${CONFIG} --multiply-reads 32 --reads-per-batch 32 --paired-mode 3 --no-no-io-reads --reads-count 125531901

#paired MP
python ./master.py --multiprocess 320000 --index $BT2_INDEX/hg19 --hisat-index $HISAT_INDEX/hg19_hisat --hisat-U $HISAT_READS --hisat-m1 $HISAT_READS_1 --hisat-m2 $HISAT_READS_2 --sensitivities s --sam-dev-null --tempdir $ROOT2 --output-dir ${1} --nthread-series $THREAD_SERIES --config ${CONFIG_MP} --multiply-reads 32 --reads-per-batch 32 --paired-mode 3 --no-no-io-reads --reads-count 125531901
