import re
import subprocess
#import threading
import bisect

import unifieddiffscanner as uds
#from dollytuple import DollyTuple

assert not hasattr(1, "__len__") 

def _call_subprocess_get_ret_and_output(args):
    p = subprocess.Popen(args, shell=False,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    output = p.stdout.readlines()
    ret = p.wait()
    return ret, output

class _SubprocessReturnValue(object):
    def __init__(self, return_value):
        self.return_value = return_value

def _call_subprocess_iter_output_and_ret(args):
    p = subprocess.Popen(args, shell=False,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    stdouterr, stdin = p.stdout, p.stdin
    
    while True:
        line = stdouterr.readline()
        if not line: break
        yield line
    ret = p.wait()
    yield _SubprocessReturnValue(ret)

def _has_extension(filename, extensions):
    for e in extensions:
        if filename.endswith(e): 
            return True

def gen_filename_filter(targetFileExtensions, targetSubdir):
    if targetSubdir: 
        assert not targetSubdir.endswith("/")
    if targetFileExtensions:
        if len(targetFileExtensions) == 1:
            ext = targetFileExtensions[0]
            if targetSubdir:
                return lambda fp: fp.startswith(subd) and fp.endswith(ext)
            else:
                return lambda fp: fp.endswith(ext)
        else:
            targetFileExtensions = targetFileExtensions[:] # make it a new copy, to prevent modification from outside
            if targetSubdir:
                subd = targetSubdir + "/"
                return lambda fp: fp.startswith(subd) and _has_extension(fp, targetFileExtensions)
            else:
                return lambda fp: _has_extension(fp, targetFileExtensions)
    else:
        if targetSubdir:
            subd = targetSubdir + "/"
            return lambda fp: fp.startswith(subd) and not fp.endswith("/")
        else:
            return lambda fp: not fp.endswith("/")

class RepositoryAccessError(ValueError): pass

def split_by_cr(lines):
    r = []
    for L in lines:
        L = L.rstrip()
        if L.find('\r'):
            r.extend(L.split('\r'))
        else:
            r.append(L)
    return r

def extract_diff_from_repository(repoFilePath, revision):
    cmd = [ "svnlook", "diff", "-r", str(revision), repoFilePath ]
    ret, output = _call_subprocess_get_ret_and_output(cmd)
    if ret != 0:
        raise RepositoryAccessError("failure in 'svnlook diff', revision %d" % revision)
    return split_by_cr(output)

def get_head_revision_from_repository(repoFilePath):
    ret, output = _call_subprocess_get_ret_and_output([ "svnlook", "youngest", repoFilePath ])
    if ret != 0: 
        raise RepositoryAccessError("can't invoke svnlook (not a repository?)")
    for line in output:
        line = line.rstrip()
        try:
            return int(line)
        except:
            raise RepositoryAccessError("invalid result of 'svnlook youngest'")

def get_file_content_from_repository(repoFilePath, revision, filePath):
    ret, output = _call_subprocess_get_ret_and_output([ "svnlook", "cat", 
            repoFilePath, "-r", "%d" % revision, filePath ])
    if ret != 0: 
        raise RepositoryAccessError("failure in 'svnlook cat', revision %d, file: %s" % ( revision, filePath ))
    return split_by_cr(output)

def get_changed_files_from_repository(repoFilePath, revision):
    ret, output = _call_subprocess_get_ret_and_output([ "svnlook", "changed", 
            repoFilePath, "-r", "%d" % revision ])
    if ret != 0: 
        raise RepositoryAccessError("failure in 'svnlook changed', revision %d" % revision)
    return [line[4:].rstrip() for line in output]

def get_changed_dirs_from_repository(repoFilePath, revision):
    ret, output = _call_subprocess_get_ret_and_output([ "svnlook", "dirs-changed", 
            repoFilePath, "-r", "%d" % revision ])
    if ret != 0: 
        raise RepositoryAccessError("failure in 'svnlook dirs-changed', revision %d" % revision)
    return [line.rstrip() for line in output]

def get_file_list_from_repoisitory(repoFilePath, revision, 
        targetFileExtensions=None, targetSubdir=None):
    isTargetFile = gen_filename_filter(targetFileExtensions, targetSubdir)
    cmd = [ "svnlook", "tree", "-r", str(revision), "--full-paths", repoFilePath ]
    if targetSubdir:
        cmd.append(targetSubdir)
    ret, output = _call_subprocess_get_ret_and_output(cmd)
    if ret != 0:
        if targetSubdir is not None:
            raise RepositoryAccessError("failure in 'svnlook tree', revision %d, targetSubdir: %s" % ( revision, targetSubdir ))
        else:
            raise RepositoryAccessError("failure in 'svnlook tree', revision %d" % revision)
    r = []
    for line in output:
        line = line.rstrip()
        if isTargetFile(line):
            r.append(line)
    return r

class ContentChangeTrackerInitializationError(ValueError): pass

class ContentChangeTracker(object):
    def __init__(self, matchPredicate, readFileFunc, matchSink=None, renameSink=None):
        self.__matchPredicate = matchPredicate # fileName, lineIndex, line
        self.__readFileFunc = readFileFunc # (rev, fileName) -> list of line
        
        if matchSink is None:
            self.__matchSink = lambda rev, fileName, lineIndex, line, deletedRevision=None: None
        else:
            self.__matchSink = matchSink # (rev, fileName, lineIndex, line, deletedRevision=None)
        if renameSink is None:
            self.__renameSink = lambda rev, fileName, renamedAt, newFileName: None
        else:
            self.__renameSink = renameSink # (rev, fileName, renamedAt, newFileName)
        
        self.__fileIsTargetPredicate = lambda p: True
        
        self.__fileTable = dict() # filePath -> list of ( originalRev, originalFileName, originalLineIndex, line )
        self.brokenFiles = [] # list of (rev, fileName)
        
        self.revision = None
        self.changingFilePath = None
        self.changingContent = None
        self.contentUpdateData = None
        self.changeOccured = False
        
    def set_targets(self, targetExtensions, targetSubdir):
        if targetExtensions is None and targetSubdir is None:
            self.__fileIsTargetPredicate = lambda p: True
        else:
            self.__fileIsTargetPredicate = gen_filename_filter(targetExtensions, targetSubdir)

    def set_revision(self, revision):
        self.__flush_change()
        self.revision = revision
        self.changeOccured = False
    
    def initialize_to_revision(self, revision, filePathList):
        if self.revision is not None:
            raise ContentChangeTrackerInitializationError("initialize_to_revision must be called before set_revision")
        self.revision = revision
        for f in filePathList:
            self.__read_file(revision, f)
        if filePathList:
            self.changeOccured = True
    
    def close_revision(self):
        self.__flush_change()
        for f in list(self.__fileTable.iterkeys()):
            self.__flush_file_wo_deleted_revision(f)
    
    def get_change_occured(self):
        return self.changeOccured
    
    def get_broken_files(self):
        return sorted(self.brokenFiles)
    
    def __read_file(self, revision, filePath):
        lines = self.__readFileFunc(revision, filePath)
        items = []
        for li, L in enumerate(lines):
            item = ( revision, filePath, li, L )
            items.append(item if self.__matchPredicate(*item) else None)
        self.__fileTable[filePath] = items if any(items) else len(items)
        
    def __flush_file(self, filePath):
        try:
            content = self.__fileTable.pop(filePath)
        except KeyError:
            pass
        else:
            if hasattr(content, "__len__"): # if not isinstance(content, int):
                for item in filter(None, content):
                    self.__matchSink(*item, deletedRevision=self.revision)
   
    def __flush_file_wo_deleted_revision(self, filePath):
        content = self.__fileTable.pop(filePath)
        if hasattr(content, "__len__"): # if not isinstance(content, int):
            for item in filter(None, content):
                self.__matchSink(*item)
   
    def __flush_change(self):
        if self.contentUpdateData is None: return

        contentUpdateData = self.contentUpdateData
        self.contentUpdateData = None
        
        oldContent = self.changingContent
        if not hasattr(oldContent, "__len__"): # if not isinstance(oldContent, int):
            newContent = oldContent
            for _, upd in contentUpdateData:
                if hasattr(upd, "__len__"): 
                    break # for _
                newContent += upd
            else:
                assert newContent >= 0
                if not newContent:
                    del self.__fileTable[self.changingFilePath]
                else:
                    self.__fileTable[self.changingFilePath] = newContent
                return
            oldContent = [ None ] * self.changingContent
        
        newContent = []
        lastOldIndex = 0
        for oldIndex, upd in contentUpdateData:
            assert oldIndex >= lastOldIndex
            newContent.extend(oldContent[lastOldIndex : oldIndex])
            if not hasattr(upd, "__len__"):
                if upd >= 0:
                    newContent.extend(None for _ in xrange(upd))
                else:
                    for oldItem in filter(None, oldContent[oldIndex : oldIndex - upd]):
                        self.__matchSink(*oldItem, deletedRevision=self.revision)
                    oldIndex -= upd
            else:
                for item in upd:
                    if item:
                        assert item[2] == len(newContent)
                    newContent.append(item)
            lastOldIndex = oldIndex
        else:
            newContent.extend(oldContent[lastOldIndex:])
        if not newContent:
            del self.__fileTable[self.changingFilePath]
        else:
            if not any(newContent):
                newContent = len(newContent)
            self.__fileTable[self.changingFilePath] = newContent
    
    def fileDescSink(self, oldFile, newFile):
        self.__flush_change()
        self.changingFilePath = None
        self.contentUpdateData = None
        
        oldFilePath = oldFile.split('\t')[0]
        newFilePath = newFile.split('\t')[0]
        if newFilePath != oldFilePath:
            self.__flush_file(oldFilePath)
            if self.__fileIsTargetPredicate(newFilePath):
                try:
                    self.__read_file(self.revision, newFilePath)
                except RepositoryAccessError:
                    # this case means that a file was renamed and then deleted.
                    pass
            self.changeOccured = True
            raise uds.SkipDescriptionLines
        else:
            if self.__fileIsTargetPredicate(newFilePath):
                self.changingFilePath = newFilePath
                self.contentUpdateData = []
                self.changingContent = self.__fileTable.setdefault(newFilePath, [])
                self.changeOccured = True
            else:
                raise uds.SkipDescriptionLines
            
    def changeSink(self, oldRange, newRange, markAndLines):
        if self.contentUpdateData is None: 
            return
        groupedSplittedChanges = uds.grouped_split_change(oldRange, newRange, markAndLines)
        if self.changingContent == [] and oldRange != ( 0, 0 ):
            self.brokenFiles.append(( self.revision, self.changingFilePath ))
            return
        for oRange, delLines, nRange, addLines in groupedSplittedChanges:
            if oRange == ( 0, 0 ): oRange = ( 1, 0 )
            oldIndex = oRange[0] - 1
            newIndex = nRange[0] - 1
            if delLines:
                lenDelLines = len(delLines)
                self.contentUpdateData.append(( oldIndex, -lenDelLines ))
                oldIndex += lenDelLines
            if addLines:
                items = [( self.revision, self.changingFilePath, newIndex + i, L ) \
                        for i, L in enumerate(addLines)]
                items = [(item if self.__matchPredicate(*item) else None) for item in items]
                self.contentUpdateData.append(( oldIndex, items if any(items) else len(items) ))
                newIndex += len(items)
            assert oldIndex == oRange[0] - 1 + oRange[1]
            assert newIndex == nRange[0] - 1 + nRange[1]

def main():
    import sys
    import getopt
    
    usage = """
Usage: hag OPTIONS <repository> <pattern>
  Searches pattern in source files in the repository.
  The specified pattern will be searched within a line, not over two or 
  more lines.
Options
  -e <extension>,...: target extensions.
  -o output:
  -r <start>:<end>: range of revision. start must be < end.
  -r <start>: same as -r start:end, where end is HEAD revision.
  -s <subdir>: target sub-directory.
  -v: verbose.
  -w pattern: pattern is not a regular expression but a word.
"""[1:-1]
    
    if len(sys.argv) == 1:
        print usage
        sys.exit(0)
    
    repoFileName = None
    patternExpression = None
    patternWord = None
    targetExtensions = None
    targetSubdir = None
    optionVerbose = None
    revRange = None, None
    outputFileName = None
    
    HEAD = object()
    
    opts, args = getopt.gnu_getopt(sys.argv[1:], "he:o:r:s:vw:")
    
    for o, v in opts:
        if o == "-h":
            print usage
            sys.exit(0)
        elif o == "-e":
            targetExtensions = v.split(",")
        elif o == "-s":
            targetSubdir = v
        elif o == "-v":
            optionVerbose = True
        elif o == "-w":
            patternWord = v
        elif o == "-r":
            if v.find(":") < 0:
                revStart, revEnd = v, None
            else:
                revStart, revEnd = v.split(":")
            revStart = HEAD if revStart == "HEAD" else int(revStart) if revStart else None
            revEnd = HEAD if revEnd == "HEAD" else int(revEnd) if revEnd else None
            if revStart is None and revEnd is None:
                raise SystemError("empty range is given for option -r")
            revRange = ( revStart, revEnd )
        elif o == "-o":
            outputFileName = v
        else:
            assert False
    for a in args:
        if repoFileName is None:
            repoFileName = a
        elif patternExpression is None:
            patternExpression = a
        else:
            raise SystemError("too many command-line arguments")
    
    if not repoFileName:
        raise SystemError("no repository given")
    
    if not patternExpression and not patternWord:
        raise SystemError("no pattern given")
    
    if optionVerbose:
        def verbose(message):
            print >> sys.stderr, message
    else:
        def verbose(message): pass
        
    if patternExpression:
        pat = re.compile(patternExpression)
        def matchPredicate(revision, fileName, lineIndex, line): return pat.search(line)
    elif patternWord:
        def matchPredicate(revision, fileName, lineIndex, line): return line.find(patternWord) >= 0
    else:
        assert False
    
    headRev = get_head_revision_from_repository(repoFileName)
    if not revRange:
        revRange = ( 0, headRev )
    if revRange[0] is HEAD: revRange = ( headRev, revRange[1] )
    if revRange[1] is HEAD: revRange = ( revRange[0], headRev )
    if revRange[0] is None: revRange = ( 0, revRange[1] )
    if revRange[1] is None: revRange = ( revRange[0], headRev )
    revRange = list(revRange)
    for ri, r in enumerate(revRange):
        if r < 0: revRange[ri] = headRev + r
    revRange = tuple(revRange)
    
    #matchTable = dict() # set of ( originalRev, originalFileName, originalLineIndex ) -> line
    
    if outputFileName is not None:
        output = open(outputFileName, "w")
        write = output.write
    else:
        write = sys.stdout.write
    
    def matchSink(rev, fileName, lineIndex, line, deletedRevision=None):
        if deletedRevision is None:
            write("+\t%d %s %d\t%s\n" % ( rev, fileName, lineIndex + 1, line.strip() ))
        else:
            write("%d\t%d %s %d\t%s\n" % ( deletedRevision, rev, fileName, lineIndex + 1, line.strip() ))

    def renameSink(rev, fileName, renamedAt, newFileName):
        write("rename\t%d %s\t%d %s" % ( renamedAt, newFileName, rev, fileName ))
    
    def readFileFunc(rev, fileName):
        return get_file_content_from_repository(repoFileName, rev, fileName)        
    
    cct = ContentChangeTracker(matchPredicate, readFileFunc, matchSink, renameSink)
    cct.set_targets(targetExtensions, targetSubdir)
    revision = revRange[0]
    if revision > 0:
        verbose('> initializing to rev %d' % revision)
        try:
            fileList = get_file_list_from_repoisitory(repoFileName, revision, \
                    targetFileExtensions=targetExtensions, targetSubdir=targetSubdir)
        except RepositoryAccessError:
            fileList = []
        cct.initialize_to_revision(revision, fileList)
    
#    # including bug
#    # (1) when a directory is moved, the following routine can't track it.
#    if targetSubdir:
#        subd = targetSubdir + "/"
#        def canIncludeTarget(revision):
#            dirNames = get_changed_dirs_from_repository(repoFileName, revision)
#            dirNames.sort()
#            i = bisect.bisect_left(dirNames, subd)
#            return i < len(dirNames) and dirNames[i].startswith(subd)
         
#    class ReadDiffThread(threading.Thread):
#        def __init__(self, revision):
#            threading.Thread.__init__(self)
#            self.revision = revision
#        def run(self):
#            self.result = extract_diff_from_repository(repoFileName, self.revision)
#            
#    lastRevision = revRange[1] + 1
#    diffDescriptionPrefetcher = None
#    for revision in xrange(revRange[0] + 1, lastRevision):
#        cct.set_revision(revision)
#        verbose('> searching in rev %d' % revision)
#        if diffDescriptionPrefetcher is None:
#            diffDescription = extract_diff_from_repository(repoFileName, revision)
#        else:
#            diffDescriptionPrefetcher.join()
#            diffDescription = diffDescriptionPrefetcher.result
#        if revision + 1 < lastRevision:
#            diffDescriptionPrefetcher = ReadDiffThread(revision + 1)
#            diffDescriptionPrefetcher.start()
#        uds.unified_diff_scanner(diffDescription, cct.fileDescSink, cct.changeSink)

    lastRevision = revRange[1] + 1
    for revision in xrange(revRange[0] + 1, lastRevision):
        #if not canIncludeTarget(revision): continue
        cct.set_revision(revision)
        verbose('> searching in rev %d' % revision)
        diffDescription = extract_diff_from_repository(repoFileName, revision)
        uds.unified_diff_scanner(diffDescription, cct.fileDescSink, cct.changeSink)
    
    cct.close_revision()
    
    if outputFileName is not None:
        output.close()
        
if __name__ == '__main__':
    main()
    
