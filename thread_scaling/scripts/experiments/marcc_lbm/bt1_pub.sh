#!/bin/bash

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
rsync -av $ROOT1/ERR050082_2.fastq.shuffled2.fq.block  $ROOT2/


#export BT2_READS=$ROOT2/ERR050082_1.fastq.shuffled.fq.block 
#use the extended version to have enough reads for bowtie
#this is the whole ~42m reads from the original catted
#with the first 30m reads again at the end to get ~72m reads
export BT2_READS=$ROOT2/ERR050082_1.fastq.shuffled2_extended.fq.block
#export BT2_READS_1=$ROOT2/ERR050082_1.fastq.shuffled2.fq.block 
export BT2_READS_1=$ROOT2/ERR050082_1.fastq.shuffled2_extended.fq.block
export BT2_READS_2=$ROOT2/ERR050082_2.fastq.shuffled2.fq.block 

CONFIG=bt1_pub.tsv
CONFIG_MP=bt1_pub_mp.tsv
CONFIG_MP2=bt1_pub_mp2.tsv

./experiments/marcc_lbm/run_mp_mt_bt1.sh > run_mp_mt_bt1.run 2>&1

python ./master.py --reads-per-thread 450000 --index $BT2_INDEX/hg19 --hisat-index $HISAT_INDEX/hg19_hisat --U $BT2_READS --m1 $BT2_READS_1 --m2 $BT2_READS_2 --sensitivities s --sam-dev-null --tempdir $ROOT2 --output-dir ${1} --nthread-series 1,4,8,12,16,20,24,28,32,36,40,44,48,56,60,68,76,84,92,96,100,104,108 --config ${CONFIG} --multiply-reads 60 --reads-per-batch 32 --paired-mode 2 --no-no-io-reads --shorten-reads

python ./master.py --multiprocess 450000 --index $BT2_INDEX/hg19 --hisat-index $HISAT_INDEX/hg19_hisat --U $BT2_READS --m1 $BT2_READS_1 --m2 $BT2_READS_2 --sensitivities s --sam-dev-null --tempdir $ROOT2 --output-dir ${1} --nthread-series 1,4,8,12,16,20,24,28,32,36,40,44,48,56,60,68,76,84,92,96,100,104,108 --config ${CONFIG_MP} --multiply-reads 60 --reads-per-batch 32 --paired-mode 2 --no-no-io-reads --shorten-reads

python ./master.py --reads-per-thread 180000 --index $BT2_INDEX/hg19 --hisat-index $HISAT_INDEX/hg19_hisat --U $BT2_READS --m1 $BT2_READS_1 --m2 $BT2_READS_2 --sensitivities s --sam-dev-null --tempdir $ROOT2 --output-dir ${1} --nthread-series 1,4,8,12,16,20,24,28,32,36,40,44,48,56,60,68,76,84,92,96,100,104,108 --config ${CONFIG} --multiply-reads 6 --reads-per-batch 32 --paired-mode 3 --no-no-io-reads --shorten-reads

python ./master.py --multiprocess 180000 --index $BT2_INDEX/hg19 --hisat-index $HISAT_INDEX/hg19_hisat --U $BT2_READS --m1 $BT2_READS_1 --m2 $BT2_READS_2 --sensitivities s --sam-dev-null --tempdir $ROOT2 --output-dir ${1} --nthread-series 1,4,8,12,16,20,24,28,32,36,40,44,48,56,60,68,76,84,92,96,100,104,108 --config ${CONFIG_MP} --multiply-reads 6 --reads-per-batch 32 --paired-mode 3 --no-no-io-reads --shorten-reads

#UNP out MP
python ./master.py --multiprocess 180000 --index $BT2_INDEX/hg19 --hisat-index $HISAT_INDEX/hg19_hisat --U $BT2_READS --m1 $BT2_READS_1 --m2 $BT2_READS_2 --sensitivities s --sam-dev-null --tempdir $ROOT2 --output-dir ${1} --nthread-series 1,4,8,12,16,20,24,28,32,36,40,44,48,56,60,68,76,84,92,96,100,104,108 --config ${CONFIG_MP2} --multiply-reads 10 --reads-per-batch 32 --paired-mode 2 --no-no-io-reads --shorten-reads
