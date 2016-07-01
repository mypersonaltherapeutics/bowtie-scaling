"""
Master script for setting up thread-scaling experiments with Bowtie, Bowtie 2
and HISAT.
"""

from __future__ import print_function
import os
import sys
import shutil
import argparse
import subprocess
import tempfile
import re
import multiprocessing

LINES_PER_FASTQ_REC = 4
#used as the base size of the input read set to be repeated
#user may pass in their own read set files 
#whose read count will override this #
DEFAULT_BASE_READS_COUNT = 10000

#for paired mode
UNPAIRED_ONLY = 2
PAIRED_ONLY = 3

#for multiprocess mode
MP_DISABLED=0
MP_SHARED=1
MP_SEPARATE=2

#for prog (no-io mode)
#so we don't have to generate reads
#for all 3 when we only want 1
BOWTIE_PROG=1
BOWTIE2_PROG=2
HISAT_PROG=3

def mkdir_quiet(dr):
    """ Create directories needed to ensure 'dr' exists; no complaining """
    import errno
    if not os.path.isdir(dr):
        try:
            os.makedirs(dr)
        except OSError as exception:
            if exception.errno != errno.EEXIST:
                raise


def get_num_cores():
    """ Get # cores on this machine, assuming we have /proc/cpuinfo """
    p = subprocess.Popen("grep 'processor\s*:' /proc/cpuinfo | wc -l", shell=True,
                         stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE)
    out, err = p.communicate()
    assert len(err) == 0
    ncores = int(out.split()[0])
    return ncores


def get_num_nodes():
    """ Get # NUMA nodes on this machine, assuming numactl is available """
    p = subprocess.Popen('numactl -H | grep available', shell=True,
                         stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE)
    out, err = p.communicate()
    assert len(err) == 0
    nnodes = int(out.split()[1])
    return nnodes


def tool_exe(tool):
    if tool == 'bowtie2':
        return 'bowtie2-align-s'
    elif tool == 'bowtie':
        return 'bowtie-align-s'
    elif tool == 'hisat':
        return 'hisat-align-s'
    else:
        raise RuntimeError('Unknown tool: "%s"' % tool)


def tool_ext(tool):
    if tool == 'bowtie2':
        return 'bt2'
    elif tool == 'bowtie':
        return 'ebwt'
    elif tool == 'hisat':
        return 'bt2'
    else:
        raise RuntimeError('Unknown tool: "%s"' % tool)


def tool_repo(tool, args):
    if tool == 'bowtie2':
        return args.bowtie2_repo
    elif tool == 'bowtie':
        return args.bowtie_repo
    elif tool == 'hisat':
        return args.hisat_repo
    else:
        raise RuntimeError('Unknown tool: "%s"' % tool)


def make_tool_version(name, tool, preproc):
    """ Builds target in specified clone """
    exe = tool_exe(tool)
    cmd = "make -C build/%s %s %s" % (name, preproc, exe)
    print('  command: ' + cmd, file=sys.stderr)
    ret = os.system(cmd)
    if ret != 0:
        raise RuntimeError('non-zero return from make for %s version "%s"' % (tool, name))


def install_tool_version(name, tool, url, branch, preproc, build_dir='build', make_tool=True):
    """ Clones appropriate branch """
    mkdir_quiet(os.path.join(build_dir, name))
    cmd = "git clone -b %s %s %s/%s" % (branch, url, build_dir, name)
    print('  command: ' + cmd, file=sys.stderr)
    ret = os.system(cmd)
    if ret != 0:
        raise RuntimeError('non-zero return from git clone for %s version "%s"' % (tool, name))
    if make_tool:
        make_tool_version(name, tool, preproc)


def get_configs(config_fn):
    """ Generator that parses and yields the lines of the config file """
    with open(config_fn) as fh:
        for ln in fh:
            toks = ln.split('\t')
            if toks[0] == 'name' and toks[1] == 'tool' and toks[2] == 'branch':
                continue
            if len(toks) == 0 or ln.startswith('#'):
                continue
            if len(toks) == 4:
                name, tool, branch, preproc = toks
                yield name, tool, branch, preproc.rstrip(), None
            else:
                name, tool, branch, preproc, args = toks
                yield name, tool, branch, preproc, args.rstrip()


def verify_index(basename, tool):
    """ Check that all index files exist """
    te = tool_ext(tool)
    def _ext_exists(ext):
        return os.path.exists(basename + ext)
    print('  checking for "%s"' % (basename + '.1.' + te), file=sys.stderr)
    ret = _ext_exists('.1.' + te) and \
          _ext_exists('.2.' + te) and \
          _ext_exists('.3.' + te) and \
          _ext_exists('.4.' + te) and \
          _ext_exists('.rev.1.' + te) and \
          _ext_exists('.rev.2.' + te)
    if tool == 'hisat':
        return ret and _ext_exists('.5.' + te) and \
                       _ext_exists('.6.' + te) and \
                       _ext_exists('.rev.5.' + te) and \
                       _ext_exists('.rev.6.' + te)
    return ret


def verify_reads(fns):
    """ Check that files exist """
    for fn in fns:
        if not os.path.exists(fn) or not os.path.isfile(fn):
            raise RuntimeError('No such reads file as "%s"' % fn)
    return True


def gen_thread_series(args, ncpus):
    """ Generate list with the # threads to use in each experiment """
    if args.nthread_series is not None:
        series = map(int, args.nthread_series.split(','))
    elif args.nthread_pct_series is not None:
        pcts = map(lambda x: float(x)/100.0, args.nthread_pct_series.split(','))
        series = map(lambda x: int(round(x * ncpus)), pcts)
    else:
        series = [ncpus]
    return series


def count_reads(fns):
    """ Count the total number of reads in one or more fastq files """
    nlines = 0
    for fn in fns:
        with open(fn) as fh:
            for _ in fh:
                nlines += 1
    return nlines / 4

#seqs_to_cat is just for compatibility with cat_shorten's signature
def cat(fns, dest_fn, n, seqs_to_cat=0):
    """ Concatenate one or more read files into one output file """
    with open(dest_fn, 'wb') as ofh:
        for _ in range(n):
            for fn in fns:
                with open(fn,'rb') as fh:
                    shutil.copyfileobj(fh, ofh, 1024*1024*10)

def shorten(source_fns, dest_fn, input_cmd='cat %s'):
    """ Reduces the read/qual length by half (100bp=>50bp); used for shorter read aligners (bowtie) """
    output_fn = dest_fn
    os.system((input_cmd % ' '.join(source_fns)) + " | awk -f shorten.awk > %s" % output_fn)
    return output_fn


def cat_shorten(fns, dest_fn, n, seqs_to_cat=0):
    """ Concatenate one or more read files into one output file """
    if os.path.exists(dest_fn):
        os.remove(dest_fn)
    if os.path.exists(dest_fn + ".short"):
        os.remove(dest_fn + ".short")
    #if # of lines are requested, don't do a copy as well
    if seqs_to_cat > 0:
        input_cmd = 'head -%d' % (LINES_PER_FASTQ_REC*seqs_to_cat)
        output_fn = shorten(fns, dest_fn, input_cmd=(input_cmd + " %s"))
        os.rename(output_fn, dest_fn)
    else:
        output_fn = shorten(fns, "%s.short" % dest_fn)
        cat([output_fn], dest_fn, n)

def split_read_set(source_path, dest_dir, reads_per_file, nfiles, shorten_first=False):
    """ Similar to the split command, but stops after nfiles has been reached; also supports shortening first """
    (source_dir, source_fn) = os.path.split(source_path)
    dest_path = os.path.join(dest_dir, source_fn)
    fctr = 0
    rctr = 0
    fout = None
    infix = ''
    if shorten_first:
        total_lines_needed = LINES_PER_FASTQ_REC * reads_per_file * nfiles
        input_cmd = "head -%d" % total_lines_needed
        source_path = shorten([source_path], "%s.short" % dest_path, input_cmd=input_cmd + " %s")
        infix = '.short'
    lines_per_file_limit = LINES_PER_FASTQ_REC * reads_per_file
    with open(source_path,"r") as fin:
       for line in fin:
           line = line.rstrip()
           if(rctr % lines_per_file_limit == 0):
               if fout:
                   fout.close()
               fctr+=1
               if fctr > nfiles:
                   break
               fout = open("%s%s.%d.fq" % (dest_path, infix, fctr),"w")
           rctr+=1
           fout.write("%s\n" % (line))
    if fout:
        fout.close()

def copy_read_set(source_path, dest_dir, num_cats, nfiles, shorten_first=False):
    """ Sister method to split_read_set for no-io derived reads which need to be repeated first """
    (source_dir, source_fn) = os.path.split(source_path)
    dest_path = os.path.join(dest_dir, source_fn)
    cat_func = cat
    infix = ''
    if shorten_first:
        cat_func = cat_shorten
        infix = '.short'
    intermediate_path = "%s.inter" % (dest_path)
    cat_func([source_path], intermediate_path, num_cats)    
    for i in xrange(1, nfiles+1):
        with open("%s%s.%d.fq" % (dest_path, infix, i),'wb') as ofh:
            with open(intermediate_path, 'rb') as fh:
                shutil.copyfileobj(fh, ofh, 1024*1024*10)

def calculate_read_partitions(args, max_threads, tool, input_fns, tmpfiles, multiply_reads, paired_end_factor, generate_reads):
    """ Determines the following: 1) # of reads per thread 2) # of base units of copy (for catting) 3) do we need to generate reads 4) which read files to use as source """
    multiplier = multiply_reads
    if args.shorten_reads or tool == 'bowtie':
        short_read_multiplier = int(round(args.short_factor))
        multiplier *= short_read_multiplier
    paired_end_divisor = int(round(1.0 / paired_end_factor))
    nreads = DEFAULT_BASE_READS_COUNT
    nreads_per_thread = nreads * multiplier / paired_end_divisor
    #this is the unit of reads to repeat for repetitive read generation
    #if not paired, paired_end_divisor is just 1
    nreads_full = multiplier * max_threads / paired_end_divisor

    if args.reads_per_thread > 0:
        nreads_per_thread = args.reads_per_thread / paired_end_divisor

    if input_fns[0]:
        nreads = args.reads_count
        if nreads <= 0:
            nreads = count_reads([input_fns[0]])
        #sepcial case where the # of reads is greater than our base unit of concatenation
        #means we don't do an catting/repeating  
        if nreads > DEFAULT_BASE_READS_COUNT:
            #assume we've been passed enough reads in the origin file for the max thread count in the series
            #and therefore no need to copy/repeat reads to up the total number
            generate_reads = False
            if paired_end_factor < 1:
                tmpfiles[0] = input_fns[0]
                tmpfiles[1] = input_fns[1]
            else:
                tmpfiles[0] = input_fns[0]
            assert args.reads_per_thread > 0
            #if we;re just pulling reads from a full source file (no repeats)
            #just set this to be the entire set we'll need
            nreads_full = nreads_per_thread * max_threads
    print('  counted %d paired-end reads, %d for a full series w/, %d reads per thread, %d threads (multiplier=%f, divisor=%d), generating reads? %s' %
          (nreads, nreads_full, nreads_per_thread, max_threads, multiplier, paired_end_divisor, generate_reads), file=sys.stderr)

    return (generate_reads, tmpfiles, nreads_per_thread, nreads_full)



def prepare_mp_reads(args, tmpdir, max_threads, tool, args_U, args_m1, args_m2, generate_reads=True):
    """ Calculates read units (e.g. # per thread) and possibly generates read sets for multiprocess aligning """

    #if we've been passed in the # of reads explicitly
    #set it here and assume the original files are also 
    #fine for using for a source
    nreads_unp_per_thread = args.multiprocess
    nreads_pe_per_thread = args.multiprocess / 2
    tmpfile = args_U
    tmpfile_1 = args_m1
    tmpfile_2 = args_m2    

    #if we didn't get the explicit # of reads per unp process passed in
    #do the default/normal calculation based on multiply-reads and related options
    #**but** we don't care about re-assigning the input reads files as in the multithread case
    if args.multiprocess <= MP_SEPARATE:
        print('Counting %s reads' % tool, file=sys.stderr)
        generic_args_U = args_U
        #caculate for single end reads (tmpfile_1 and tmpfile_2 don't change)
        (_, _, nreads_unp_per_thread, nreads_unp_full) = \
            calculate_read_partitions(args, max_threads, tool, [args_U], [tmpfile], 
                      args.multiply_reads, 1, generate_reads)
    
        #now calculate for paired ends (same as above except tmpfile doesn't change)
        (_, _, nreads_pe_per_thread, nreads_pe_full) = \
            calculate_read_partitions(args, max_threads, tool, [args_m1, args_m2], [tmpfile_1, tmpfile_2], 
                     args.multiply_reads, args.paired_end_factor, generate_reads)

    shorten = args.shorten_reads or tool == 'bowtie'    
 
    if generate_reads:
        #use a large FASTQ file as the split source
        if args.no_no_io_reads:
            if args.paired_mode != PAIRED_ONLY:
                split_read_set(args_U, tmpdir, nreads_unp_per_thread, max_threads, shorten_first=shorten)

		(tmpfile_dir, tmpfile) = os.path.split(tmpfile)
                tmpfile = os.path.join(tmpdir, tmpfile)
            if args.paired_mode != UNPAIRED_ONLY:
                split_read_set(args_m1, tmpdir, nreads_pe_per_thread, max_threads, shorten_first=shorten)
                split_read_set(args_m2, tmpdir, nreads_pe_per_thread, max_threads, shorten_first=shorten)
		
                (tmpfile_dir, tmpfile_1) = os.path.split(tmpfile_1)
                tmpfile_1 = os.path.join(tmpdir, tmpfile_1)
		(tmpfile_dir, tmpfile_2) = os.path.split(tmpfile_2)
                tmpfile_2 = os.path.join(tmpdir, tmpfile_2)
        #use the (probably) small set of noio reads as the copy source
        else:
            if args.paired_mode != PAIRED_ONLY:
                copy_read_set(args_U, tmpdir, nreads_unp_per_thread / DEFAULT_BASE_READS_COUNT, max_threads, shorten_first=shorten)
            if args.paired_mode != UNPAIRED_ONLY:
                copy_read_set(args_m1, tmpdir, nreads_pe_per_thread / DEFAULT_BASE_READS_COUNT, max_threads, shorten_first=shorten)
                copy_read_set(args_m2, tmpdir, nreads_pe_per_thread / DEFAULT_BASE_READS_COUNT, max_threads, shorten_first=shorten)
    infix = ''
    if shorten:
        infix = '.short'
    tmpfile = tmpfile + infix + ".%d.fq" 
    tmpfile_1 = tmpfile_1 + infix + ".%d.fq" 
    tmpfile_2 = tmpfile_2 + infix + ".%d.fq" 

    return tmpfile, tmpfile_1, tmpfile_2, nreads_unp_per_thread, nreads_pe_per_thread


def prepare_reads(args, tmpdir, max_threads, tool, args_U, args_m1, args_m2, generate_reads=True):
    """ Calculates read units (e.g. # per thread) and possibly generates read sets for multithread aligning """

    tmpfile = os.path.join(tmpdir, tool + '_' + "reads.fq")
    tmpfile_1 = os.path.join(tmpdir, tool + '_' + "reads_1.fq")
    tmpfile_2 = os.path.join(tmpdir, tool + '_' + "reads_2.fq")

    print('Counting %s reads' % tool, file=sys.stderr)

    #caculate for single end reads (tmpfile_1 and tmpfile_2 don't change)
    (generate_reads, tmpfiles, nreads_unp_per_thread, nreads_unp_full) = \
        calculate_read_partitions(args, max_threads, tool, [args_U], [tmpfile], 
                  args.multiply_reads, 1, generate_reads)
    tmpfile = tmpfiles[0]

    #now calculate for paired ends (same as above except tmpfile doesn't change)
    (generate_reads, tmpfiles, nreads_pe_per_thread, nreads_pe_full) = \
        calculate_read_partitions(args, max_threads, tool, [args_m1, args_m2], [tmpfile_1, tmpfile_2], 
                  args.multiply_reads, args.paired_end_factor, generate_reads)
    tmpfile_1 = tmpfiles[0]
    tmpfile_2 = tmpfiles[1]

    cat_func = cat
    #default=0 means everything
    seqs_to_cat_unp = 0
    seqs_to_cat_pe = 0
    #special case: short reads (e.g. bowtie) get generated even with an
    #intact, full-sized original souce file, but not if explicitly told NOT
    #to generate reads
    if (args.shorten_reads or tool == 'bowtie') and not args.no_reads:
        tmpfile = os.path.join(tmpdir, tool + '_' + "reads_short.fq")
        tmpfile_1 = os.path.join(tmpdir, tool + '_' + "reads_1_short.fq")
        tmpfile_2 = os.path.join(tmpdir, tool + '_' + "reads_2_short.fq")
        #we still have to generate reads for short alingerxs (bowtie) no matter what
        if not generate_reads:
            generate_reads = True
            #have to explicitly limit # of sequences to pull from source file
            seqs_to_cat_unp = nreads_unp_full
            seqs_to_cat_pe = nreads_pe_full
        cat_func = cat_shorten

    #need the default generate reads to be still enabled OR an explicit setting of shorten_reads
    #doesn't include an OR check here for bowtie as the tool since there will be times when 
    #we're running for bowtie but don't want to actually generate reads
    if generate_reads:
        if args.paired_mode != PAIRED_ONLY:
            print('Concatenating new unpaired long-read file of %d reads and storing in "%s"' % (nreads_unp_full,tmpfile), file=sys.stderr)
            cat_func([args_U], tmpfile, nreads_unp_full, seqs_to_cat=seqs_to_cat_unp)
        #paired
        if args.paired_mode != UNPAIRED_ONLY:
            print('Concatenating new long paired-end mate 1s of %d reads and storing in "%s"' % (nreads_pe_full, tmpfile_1), file=sys.stderr)
            cat_func([args_m1], tmpfile_1, nreads_pe_full, seqs_to_cat=seqs_to_cat_pe)
            print('Concatenating new long paired-end mate 2s of %d reads and storing in "%s"' % (nreads_pe_full, tmpfile_2), file=sys.stderr)
            cat_func([args_m2], tmpfile_2, nreads_pe_full, seqs_to_cat=seqs_to_cat_pe)

    return tmpfile, tmpfile_1, tmpfile_2, nreads_unp_per_thread, nreads_pe_per_thread


non_fastq_line = re.compile('^\s*[\{\}]')
def extract_noio_reads(tool,rawseqs_filepath,tmpdir,suffix,generate_reads=True):
    """ Does the extraction of the compiled in reads from the header files of the tool for both single and paired reads """
    fout_name = "%s/%s.noio%s.fastq" % (tmpdir,tool,suffix)
    if not generate_reads:
        return fout_name
    with open(fout_name,'w') as fout:
        ctr=0
        with open("%s" % (rawseqs_filepath),"r") as fin:
            for line in fin:
                line = line.rstrip()
                if '  {' in line or '  },' in line:
                    continue
                ctr+=1
                #remove c-language related characters (escapes, braces)
                line = re.sub(r'^\s*"','',line)
                line = re.sub(r'",?\s*$','',line)
                line = re.sub(r'\\\?','?',line)
                line = re.sub(r'\\"','"',line)
                if ctr % 3 == 1:
                    fout.write("@")
                    if suffix == '2':
                        #adjust for pairing
                        line = re.sub(r'\/1$','\/2',line)
                elif ctr % 3 == 0:
                    fout.write("+\n")
                fout.write("%s\n" % (line))
    return fout_name


def setup_noio_reads(args,tmpdir,tool,name,generate_reads=True):
    """ Temporarily clones and extracts compiled-in reads from the no-io branch of the given tool """
    branch = 'no-io'
    temp_build_dir = tmpdir
    multiply_factor = 5
    if generate_reads:
        install_tool_version(name, tool, tool_repo(tool, args), branch, None, build_dir=temp_build_dir, make_tool=False)
    seqs_fname = 'rawseqs'
    rawseqs_filepath = "%s/%s/%s.h" % (temp_build_dir, name, seqs_fname)
    rawseqs_fastq = extract_noio_reads(tool,rawseqs_filepath,tmpdir,'',generate_reads=generate_reads)
    rawseqs_filepath = "%s/%s/%s_1.h" % (temp_build_dir, name, seqs_fname)
    rawseqs_fastq_p1 = extract_noio_reads(tool,rawseqs_filepath,tmpdir,'1',generate_reads=generate_reads)
    rawseqs_filepath = "%s/%s/%s_2.h" % (temp_build_dir, name,seqs_fname)
    rawseqs_fastq_p2 = extract_noio_reads(tool,rawseqs_filepath,tmpdir,'2',generate_reads=generate_reads)

    rawseqs_fastq_10k = "%s.10k.fq" % (rawseqs_fastq)
    rawseqs_fastq_p1_10k = "%s.10k.fq" % (rawseqs_fastq_p1)
    rawseqs_fastq_p2_10k = "%s.10k.fq" % (rawseqs_fastq_p2)
   
    if generate_reads:
        shutil.rmtree("%s/%s" % (temp_build_dir,name))
        cat([rawseqs_fastq], rawseqs_fastq_10k, multiply_factor)
        os.remove(rawseqs_fastq)
        cat([rawseqs_fastq_p1], rawseqs_fastq_p1_10k, multiply_factor)
        os.remove(rawseqs_fastq_p1)
        cat([rawseqs_fastq_p2], rawseqs_fastq_p2_10k, multiply_factor)
        os.remove(rawseqs_fastq_p2)

    return (rawseqs_fastq_10k,rawseqs_fastq_p1_10k,rawseqs_fastq_p2_10k)

def run_subprocess(cmd_):
     subprocess.Popen(cmd_,shell=True,bufsize=-1)

def consolidate_mp_output(output_path):
    output_path_glob = output_path % "_*"
    output_path_final = output_path % ""
    os.system('ls %s | xargs cat >> %s' % (output_path_glob,output_path_final))
    #os.system('rm %s' % (output_path_glob))

def run_cmd(cmd, odir, nthreads, nthreads_total, paired, args):
    #if we're running with multiprocess
    if args.multiprocess != MP_DISABLED:
       #pool = multiprocessing.Pool(processes=nthreads_total)
       #pool.map(run_subprocess,[cmd] * nthreads_total)
       running = []
       for thread in (xrange(0,nthreads_total)):
           #cmd_ = "%s.%d" % (cmd,thread)
           cmd_ = cmd
           if args.multiprocess >= MP_SEPARATE:
               if paired:
                   cmd_ = cmd_ % (thread+1,thread+1,"_%d" % (thread+1))
               else:
                   cmd_ = cmd_ % (thread+1,"_%d" % (thread+1))
           print(cmd_)
           subp = subprocess.Popen(cmd_,shell=True,bufsize=-1)
           running.append(subp)
       for (i,subp) in enumerate(running):
           ret = subp.wait()
           if ret !=0:
               return ret
       with open(os.path.join(odir, 'cmd_%d.sh' % nthreads_total), 'a') as ofh:
           ofh.write(cmd + "\n")
       return 0
    ret = os.system(cmd)
    with open(os.path.join(odir, 'cmd_%d.sh' % nthreads), 'w') as ofh:
        ofh.write("#!/bin/sh\n")
        ofh.write(cmd + "\n")
    return ret


def go(args):
    #make sure we either are going to generate from no-io or have passed in sequence read files
    #this allows flexibility where the user may pass in read files but prefer to use the generated no-io reads anyway
    if args.no_no_io_reads and not ((args.U and args.m1 and args.m2) or (args.hisat_U and args.hisat_m1 and args.hisat_m2)):
        sys.stderr.write("--no-no-io-reads cannot be true if one or more of the read file arguments (U,m1,m2,hiast-U,hisat-m1,hisat-m2) is also not specified")
        sys.exit(-1)
    nnodes, ncpus = get_num_nodes(), get_num_cores()
    print('# NUMA nodes = %d' % nnodes, file=sys.stderr)
    print('# CPUs = %d' % ncpus, file=sys.stderr)

    sensitivity_map = {'vs': '--very-sensitive',
                       'vsl': '--very-sensitive-local',
                       's': '--sensitive',
                       'sl': '--sensitive-local',
                       'f': '--fast',
                       'fl': '--fast-local',
                       'vf': '--very-fast',
                       'vfl': '--very-fast-local'}
    
    tmpdir = args.tempdir
    if tmpdir is None:
        tmpdir = tempfile.mkdtemp()
    if not os.path.exists(tmpdir):
        mkdir_quiet(tmpdir)
    if not os.path.isdir(tmpdir):
        raise RuntimeError('Temporary directory isn\'t a directory: "%s"' % tmpdir)

    (hisat_reads,hisat_reads_p1,hisat_reads_p2) = (None,None,None)
    (bt2_reads,bt2_reads_p1,bt2_reads_p2) = (None,None,None)
    
    if args.no_no_io_reads:
        print('getting base reads from passed in files', file=sys.stderr)
        (hisat_reads,hisat_reads_p1,hisat_reads_p2) = (args.hisat_U, args.hisat_m1, args.hisat_m2)
        (bt2_reads,bt2_reads_p1,bt2_reads_p2) = (args.U, args.m1, args.m2)
    else:
        print('generating base reads from no-io compiled headers', file=sys.stderr)
        (hisat_reads,hisat_reads_p1,hisat_reads_p2) = setup_noio_reads(args,tmpdir,'hisat','hisat-no-io', generate_reads=(not args.no_reads))
        (bt2_reads,bt2_reads_p1,bt2_reads_p2) = setup_noio_reads(args,tmpdir,'bowtie2','bt2-no-io', generate_reads=(not args.no_reads))

    print('Setting up binaries', file=sys.stderr)
    for name, tool, branch, preproc, aligner_args in get_configs(args.config):
        if name == 'name' and branch == 'branch':
            continue  # skip header line
        build, pull = False, False
        build_dir = os.path.join('build', name)
        if os.path.exists(build_dir) and args.force_builds:
            print('  Removing existing "%s" subdir because of --force' % build_dir, file=sys.stderr)
            shutil.rmtree(build_dir)
            build = True
        elif os.path.exists(build_dir):
            pull = True
        elif not os.path.exists(build_dir):
            build = True

        if pull and not args.no_pull:
            print('  Pulling "%s"' % name, file=sys.stderr)
            os.system('cd %s && git pull' % build_dir)
            make_tool_version(name, tool, preproc)
        elif build:
            print('  Building "%s"' % name, file=sys.stderr)
            install_tool_version(name, tool, tool_repo(tool, args), branch, preproc)

    print('Generating thread series', file=sys.stderr)
    series = gen_thread_series(args, ncpus)
    print('  series = %s' % str(series))

    #assumes args.multiprocess is set >1 and == nrreads to pull per thread for unpaired mode
    nr = args.multiprocess
    nr_pe = args.multiprocess/2
    tmpfile, tmpfile_short, tmpfile_1, tmpfile_short_1, tmpfile_2, tmpfile_short_2, \
        nreads_unp, nreads_pe, nreads_unp_short, nreads_pe_short = ("","","","","","",nr,nr_pe,nr,nr_pe)
    tmpfile_hs, _, tmpfile_1_hs, _, tmpfile_2_hs, _, nreads_unp_hs, nreads_pe_hs, _, _ = ("","","","","","",nr,nr_pe,nr,nr_pe)

    #if we're not running n separate multiprocesses
    prepare_reads_func = prepare_reads
    if args.multiprocess >= MP_SEPARATE:
        prepare_reads_func = prepare_mp_reads
    #either we've got bowtie1/2 input reads or none of the input file options have been set in which case
    #we generate for each aligner case (bowtie, bowtie2, and hisat) 
    if args.U or not (args.hisat_U):
        if args.shorten_reads or not (args.U or args.hisat_U or args.prog == BOWTIE2_PROG or args.prog == HISAT_PROG):
            tmpfile_short, tmpfile_short_1, tmpfile_short_2, \
                nreads_unp_short, nreads_pe_short = \
                prepare_reads_func(args, tmpdir, max(series), 'bowtie', bt2_reads, bt2_reads_p1, bt2_reads_p2, generate_reads=(not args.no_reads))
        if not args.shorten_reads or not (args.U or args.hisat_U or args.prog == BOWTIE_PROG or args.prog == HISAT_PROG):
            tmpfile, tmpfile_1, tmpfile_2, \
                nreads_unp, nreads_pe = \
                prepare_reads_func(args, tmpdir, max(series), 'bowtie2', bt2_reads, bt2_reads_p1, bt2_reads_p2, generate_reads=(not args.no_reads))
       
    if args.hisat_U or not (args.U or args.prog == BOWTIE_PROG or args.prog == BOWTIE2_PROG):
        tmpfile_hs, tmpfile_1_hs, tmpfile_2_hs, \
            nreads_unp_hs, nreads_pe_hs = \
            prepare_reads_func(args, tmpdir, max(series), 'hisat', hisat_reads, hisat_reads_p1, hisat_reads_p2, generate_reads=(not args.no_reads))

    sensitivities = args.sensitivities.split(',')
    sensitivities = zip(map(sensitivity_map.get, sensitivities), sensitivities)
    print('Generating sensitivity series: "%s"' % str(sensitivities), file=sys.stderr)

    print('Creating output directory "%s"' % args.output_dir, file=sys.stderr)
    mkdir_quiet(args.output_dir)

    print('Generating %scommands' % ('' if args.dry_run else 'and running '), file=sys.stderr)

    #allows for doing one or both paired modes
    paired_modes = []
    if args.paired_mode != PAIRED_ONLY:
	paired_modes.append(False)
    if args.paired_mode != UNPAIRED_ONLY:
	paired_modes.append(True)
    # iterate over sensitivity levels
    for sens, sens_short in sensitivities:

        # iterate over unpaired / paired-end
        for paired in paired_modes:

            # iterate over numbers of threads
            for nthreads in series:

                last_tool = ''
                # iterate over configurations
                for name, tool, branch, preproc, aligner_args in get_configs(args.config):
                    name_ = name
                    if args.no_no_io_reads:
                        name_ = "%s-id" % (name)
                    else:
                        name_ = "%s-nid" % (name)
                    odir = os.path.join(args.output_dir, name_, sens[2:], 'pe' if paired else 'unp')
                    if tool != last_tool:
                        print('Checking that index files exist', file=sys.stderr)
                        index = args.hisat_index if tool == 'hisat' else args.index
                        if not verify_index(index, tool):
                            raise RuntimeError('Could not verify index files')
                        last_tool = tool


                    if not os.path.exists(odir):
                        print('  Creating output directory "%s"' % odir, file=sys.stderr)
                        mkdir_quiet(odir)

                    # Compose command
                    runname = '%s_%s_%s_%d' % (name, 'pe' if paired else 'unp', sens_short, nthreads)
                    stdout_ofn = os.path.join(odir, '%d.txt' % nthreads)
                    sam_ofn = os.path.join(odir if args.sam_output_dir else tmpdir, '%s.sam' % runname)
                    sam_ofn = '/dev/null' if args.sam_dev_null else sam_ofn
                    cmd = ['build/%s/%s' % (name, tool_exe(tool))]
                    nthreads_total = nthreads
                    if args.multiprocess != MP_DISABLED:
                        stdout_ofn = os.path.join(odir, '%d%%s.txt' % (nthreads))
                        nthreads = 1
                    cmd.extend(['-p', str(nthreads)])
                    if 'batch_parsing' in branch:
                        cmd.extend(['--reads-per-batch', str(args.reads_per_batch)])
                    if tool == 'bowtie2' or tool == 'hisat':
                        nr_pe = nreads_pe if tool == 'bowtie2' else nreads_pe_hs
                        nr_unp = nreads_unp if tool == 'bowtie2' else nreads_unp_hs
                        nreads = (nr_pe * nthreads) if paired else (nr_unp * nthreads)
                        cmd.extend(['-u', str(nreads)])
                        cmd.append(sens)
                        cmd.extend(['-S', sam_ofn])
                        cmd.extend(['-x', index])
                        if paired:
                            cmd.extend(['-1', tmpfile_1 if tool == 'bowtie2' else tmpfile_1_hs])
                            cmd.extend(['-2', tmpfile_2 if tool == 'bowtie2' else tmpfile_2_hs])
                        else:
                            cmd.extend(['-U', tmpfile if tool == 'bowtie2' else tmpfile_hs])
                        cmd.append('-t')
                        if aligner_args is not None and len(aligner_args) > 0:  # from config file
                            cmd.extend(aligner_args.split())
                        if args.multiprocess != MP_DISABLED:
                            cmd.append('--mm')
                        cmd.extend(['>', stdout_ofn])
                    elif tool == 'bowtie':
                        nreads = (nreads_pe_short * nthreads) if paired else (nreads_unp_short * nthreads)
                        cmd.extend(['-u', str(nreads)])
                        cmd.extend([index])
                        if paired:
                            cmd.extend(['-1', tmpfile_short_1])
                            cmd.extend(['-2', tmpfile_short_2])
                        else:
                            cmd.extend([tmpfile_short])
                        cmd.extend([sam_ofn])
                        cmd.append('-t')
                        cmd.append('-S')
                        if aligner_args is not None and len(aligner_args) > 0:  # from config file
                            cmd.extend(aligner_args.split())
                        if args.multiprocess != MP_DISABLED:
                            cmd.append('--mm')
                        cmd.extend(['>', stdout_ofn])
                    else:
                        raise RuntimeError('Unsupported tool: "%s"' % tool)
                    cmd = ' '.join(cmd)
                    print(cmd)
                    run = False
                    if not args.dry_run:
                        if os.path.exists(stdout_ofn):
                            if args.force_runs:
                                print('  "%s" exists; overwriting because --force-runs was specified' % stdout_ofn, file=sys.stderr)
                                run = True
                            else:
                                print('  skipping run "%s" since output file "%s" exists' % (runname, stdout_ofn), file=sys.stderr)
                        else:
                            run = True
                    if run:
                        run_cmd(cmd, odir, nthreads, nthreads_total, paired, args)
                        if args.multiprocess == MP_DISABLED:
                            assert os.path.exists(sam_ofn)
                            if args.delete_sam and not args.sam_dev_null:
                                os.remove(sam_ofn)
                        else:
                            consolidate_mp_output(stdout_ofn)


if __name__ == '__main__':

    # Output-related options
    parser = argparse.ArgumentParser(description='Set up thread scaling experiments.')
    default_bt_repo = "https://github.com/BenLangmead/bowtie.git"
    default_bt2_repo = "https://github.com/BenLangmead/bowtie2.git"
    default_hs_repo = "https://github.com/BenLangmead/hisat.git"  # this is my fork

    requiredNamed = parser.add_argument_group('required named arguments')
    requiredNamed.add_argument('--index', metavar='index_basename', type=str, required=True,
                        help='Path to bowtie & bowtie2 indexes; omit final ".1.bt2" or ".1.ebwt".  Should usually be a human genome index, with filenames like hg19.* or hg38.*')
    requiredNamed.add_argument('--hisat-index', metavar='index_basename', type=str, required=True,
                        help='Path to HISAT index; omit final ".1.bt2".  Should usually be a human genome index, with filenames like hg19.* or hg38.*')
    requiredNamed.add_argument('--config', metavar='pct,pct,...', type=str, required=True,
                        help='Specifies path to config file giving configuration short-names, tool names, branch names, compilation macros, and command-line args.  (Provided master_config.tsv is probably sufficient)')
    requiredNamed.add_argument('--output-dir', metavar='path', type=str, required=True,
                        help='Directory to put thread timings in.')
    parser.add_argument('--U', metavar='path', type=str, required=False,
                        help='Path to file to use for unpaired reads for tools other than HISAT.  Will concatenate multiple copies according to # threads.')
    parser.add_argument('--m1', metavar='path', type=str, required=False,
                        help='Path to file to use for mate 1s for paried-end runs for tools other than HISAT.  Will concatenate multiple copies according to # threads.')
    parser.add_argument('--m2', metavar='path', type=str, required=False,
                        help='Path to file to use for mate 2s for paried-end runs for tools other than HISAT.  Will concatenate multiple copies according to # threads.')
    parser.add_argument('--hisat-U', metavar='path', type=str, required=False,
                        help='Path to file to use for unpaired reads for HISAT.  Will concatenate multiple copies according to # threads.')
    parser.add_argument('--hisat-m1', metavar='path', type=str, required=False,
                        help='Path to file to use for mate 1s for paried-end runs for HISAT.  Will concatenate multiple copies according to # threads.')
    parser.add_argument('--hisat-m2', metavar='path', type=str, required=False,
                        help='Path to file to use for mate 2s for paried-end runs for HISAT.  Will concatenate multiple copies according to # threads.')
    parser.add_argument('--nthread-series', metavar='int,int,...', type=str, required=False,
                        help='Series of comma-separated ints giving the number of threads to use.  E.g. --nthread-series 10,20,30 will run separate experiments using 10, 20 and 30 threads respectively.  Deafult: just one experiment using max # threads.')
    parser.add_argument('--multiply-reads', metavar='int', type=int, default=20,
                        help='Duplicate the input reads file this many times before scaling according to the number of reads.')
    parser.add_argument('--paired-end-factor', metavar='float', type=int, default=0.5,
                        help='For paired-end experiments, multiply base number of reads by this factor.')
    parser.add_argument('--short-factor', metavar='float', type=int, default=3.0,
                        help='For unpaired experiments, multiple base number of reads by this factor.')
    parser.add_argument('--nthread-pct-series', metavar='pct,pct,...', type=str, required=False,
                        help='Series of comma-separated percentages giving the number of threads to use as fraction of max # threads')
    parser.add_argument('--bowtie-repo', metavar='url', type=str, default=default_bt_repo,
                        help='Path to bowtie repo, which we clone for each bowtie version we test (deafult: %s)' % default_bt_repo)
    parser.add_argument('--bowtie2-repo', metavar='url', type=str, default=default_bt2_repo,
                        help='Path to bowtie2 repo, which we clone for each bowtie2 version we test (deafult: %s)' % default_bt2_repo)
    parser.add_argument('--hisat-repo', metavar='url', type=str, default=default_hs_repo,
                        help='Path to HISAT repo, which we clone for each HISAT version we test (deafult: %s)' % default_hs_repo)
    parser.add_argument('--sensitivities', metavar='level,level,...', type=str, default='s',
                        help='Series of comma-separated sensitivity levels, each from the set {vf, vfl, f, fl, s, sl, vs, vsl}.  Default: s (just --sensitive).')
    parser.add_argument('--tempdir', metavar='path', type=str, required=False,
                        help='Picks a path for temporary files.')
    parser.add_argument('--force-builds', action='store_const', const=True, default=False,
                        help='Overwrite binaries that already exist')
    parser.add_argument('--force-runs', action='store_const', const=True, default=False,
                        help='Overwrite run output files that already exist')
    parser.add_argument('--no-pull', action='store_const', const=True, default=False,
                        help='Do not git pull into the existing build directories')
    parser.add_argument('--dry-run', action='store_const', const=True, default=False,
                        help='Just verify that jobs can be run, then print out commands without running them; useful for when you need to wrap the bowtie2 commands for profiling or other reasons')
    parser.add_argument('--sam-output-dir', action='store_const', const=True, default=False,
                        help='Put SAM output in the output directory rather than in the temporary directory.  Usually we don\'t really care to examine the SAM output, so the default is reasonable.')
    parser.add_argument('--sam-dev-null', action='store_const', const=True, default=False,
                        help='Send SAM output directly to /dev/null.')
    parser.add_argument('--delete-sam', action='store_const', const=True, default=False,
                        help='Delete SAM file as soon as aligner finishes; useful if you need to avoid exhausting a partition')
    parser.add_argument('--paired-mode', metavar='int', type=int, default=1,
                        help='Which of the three modes to run: both unpaired and paired (1), unpaired only (2), paired only (3)')
    parser.add_argument('--reads-per-batch', metavar='int', type=int, default=33,
                        help='for build which use it, how many reads to lightly format, ignored for those builds which don\'t use it')
    parser.add_argument('--no-no-io-reads', action='store_const', const=True, default=False,
                        help='Don\'t Extract compiled reads from no-io branches of Bowtie2 and Hisat; instead use what\'s passed in')
    parser.add_argument('--no-reads', action='store_const', const=True, default=False,
                        help='skip read generation step; assumes reads have already been generated in the --tempdir location')
    parser.add_argument('--multiprocess', metavar='int', type=int, default=MP_DISABLED,
                        help='run n independent processes instead of n threads in one process where n is the current thread count. 0=disable, 1=use same source reads file for every process, >1=use pre-split sources files one per process and assume # passed in is the # of reads to input per process')
    parser.add_argument('--reads-per-thread', metavar='int', type=int, default=0,
                        help='set # of reads to align per thread/process directly, overrides --multiply-reads setting')
    parser.add_argument('--shorten-reads', action='store_const', const=True, default=False,
                        help='if running Bowtie or something similar set this so that generated reads will be half the normal size (e.g. 50 vs. 100 bp)')
    parser.add_argument('--reads-count', metavar='int', type=int, default=0,
                        help='set explicitly to # of reads in source reads file to avoid the cost of counting each time')
    parser.add_argument('--prog', metavar='int', type=int, default=0,
                        help='which aligner to generate reads for when using compiled in reads (no-io); overridden by the --U,--hisat-U options.  bowtie=1,bowtie2=2 (default),hisat=3')

    go(parser.parse_args())
