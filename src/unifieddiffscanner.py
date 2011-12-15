
import re

class InvaildDescription(ValueError): pass

def grouped_split_change(oldRange, newRange, markAndLines):
    oldLineNum, newLineNum = oldRange[0], newRange[0]
    r = []
    markAndLinesS = list(markAndLines) + [ ( None, None ) ]
    i = 0
    mark, line = markAndLinesS[i]
    while mark:
        if mark == ' ':
            oldLineNum += 1
            newLineNum += 1
            i += 1; mark, line = markAndLinesS[i]
        elif mark == '-':
            oldBegin = oldLineNum
            delLines = []
            while mark == '-':
                oldLineNum += 1
                delLines.append(line)
                i += 1; mark, line = markAndLinesS[i]
            newBegin = newLineNum
            addLines = []
            while mark == '+':
                newLineNum += 1
                addLines.append(line)
                i += 1; mark, line = markAndLinesS[i]
            if delLines or addLines:
                r.append(( ( oldBegin, oldLineNum - oldBegin ), delLines,
                           ( newBegin, newLineNum - newBegin ), addLines ))
        elif mark == '+':
            newBegin = newLineNum
            addLines = []
            while mark == '+':
                newLineNum += 1
                addLines.append(line)
                i += 1; mark, line = markAndLinesS[i]
            if addLines:
                r.append(( ( oldLineNum, 0 ), (),
                           ( newBegin, newLineNum - newBegin ), addLines ))
#        elif mark == '\\': # 'No newline at end of file'
#            i += 1; mark, line = markAndLinesS[i]
        else:
            raise InvaildDescription("first char of diff description must be one of +, -, ' ', \\")
    assert oldLineNum == oldRange[0] + oldRange[1]
    assert newLineNum == newRange[0] + newRange[1]
    return r

def split_change(oldRange, newRange, markAndLines):
    oldLineNum, newLineNum = oldRange[0], newRange[0]
    r = []
    markAndLinesS = list(markAndLines) + [ ( None, None ) ]
    i = 0
    mark = markAndLinesS[i][0]
    while mark:
        if mark == ' ':
            oldLineNum += 1
            newLineNum += 1
            i += 1; mark = markAndLinesS[i][0]
        elif mark in ( '+', '-' ):
            oldBegin = oldLineNum
            newBegin = newLineNum
            curMarkAndLines = []
            while mark:
                if mark == '-':
                    oldLineNum += 1
                elif mark == '+':
                    newLineNum += 1
                else:
                    break # while mark
                curMarkAndLines.append(markAndLinesS[i])
                i += 1; mark = markAndLinesS[i][0]
            if curMarkAndLines:
                r.append(( ( oldBegin, oldLineNum - oldBegin ),
                           ( newBegin, newLineNum - newBegin ), 
                           curMarkAndLines ))
#        elif mark == '\\': # 'No newline at end of file'
#            i += 1; mark = markAndLinesS[i][0]
        else:
            raise InvaildDescription("first char of diff description must be one of +, -, ' ', \\")
    assert i == len(markAndLines)
    assert oldLineNum == oldRange[0] + oldRange[1]
    assert newLineNum == newRange[0] + newRange[1]
    return r

class SkipDescriptionLines(Exception): pass

def unified_diff_scanner(lines, 
        fileDescSink=None, changeSink=None):
    if fileDescSink is None:
        fileDescSink = lambda oldFile, newFile: None
    if changeSink is None:
        changeSink = lambda oldRange, newRange, markAndLines: None
        
    patRange = re.compile("@+ -([0-9,]+) [+]([0-9,]+) @+")
    oldFile = None
    parseDescriptionLines = None
    oldLinesRemaining, newLinesRemaining = 0, 0
    rangeValues = None
    def getLineNum(lineIt):
        return len(lines) - len(list(lineIt))
    lineIt = iter([line.rstrip() for line in lines])
    lineItNext = lineIt.next
    try:
        line = lineItNext()
        while True:
            if line.startswith("--- "):
                oldFile = line[4:]
                line = lineItNext()
                if not line.startswith("+++ "):
                    raise InvaildDescription("line %d: expected '+++'" % getLineNum(lineIt))
                parseDescriptionLines = True
                try:
                    fileDescSink(oldFile, line[4:])
                except SkipDescriptionLines:
                    parseDescriptionLines = False
                oldFile = None
                line = lineItNext()
            elif line.startswith("@@"):
                if parseDescriptionLines is None:
                    raise InvaildDescription("line %d: expected '---'" % getLineNum(lineIt))
                m = patRange.match(line)
                if not m:
                    raise InvaildDescription("line %d: expected '@@'" % getLineNum(lineIt))
                rangeStrs = m.group(1), m.group(2)
                rangeValues = []
                for rangeStr in rangeStrs:
                    r = tuple(int(v) for v in rangeStr.split(","))
                    if len(r) == 1: r = ( r[0], 1 )
                    rangeValues.append(r)
                oldLinesRemaining, newLinesRemaining = rangeValues[0][1], rangeValues[1][1]
                assert  oldLinesRemaining or newLinesRemaining
                changeDescriptionLines = []
                line = lineItNext()
                if oldLinesRemaining == 0 or newLinesRemaining == 0:
                    linesRemaining = max(oldLinesRemaining, newLinesRemaining)
                    expectedMark = '+' if newLinesRemaining else '-'
                    try:
                        while linesRemaining:
                            if not line or line[0] != expectedMark:
                                if line[0:1] != '\\':
                                    raise InvaildDescription("line %d: invalid heading char in a change description line" % getLineNum(lineIt))
                            else:
                                changeDescriptionLines.append(( line[0], line[1:] ))
                                linesRemaining -= 1
                            line = lineItNext()
                    finally:
                        oldLinesRemaining = newLinesRemaining = linesRemaining
                else:
                    while oldLinesRemaining or newLinesRemaining:
                        mark = line[0] if line else ' '
                        if ' +-'.find(mark) < 0:
                            if mark != '\\':
                                raise InvaildDescription("line %d: invalid heading char in a change description line" % getLineNum(lineIt))
                        else:
                            if mark == '-':
                                oldLinesRemaining -= 1
                            elif mark == '+':
                                newLinesRemaining -= 1
                            elif mark == ' ':
                                oldLinesRemaining -= 1
                                newLinesRemaining -= 1
                            changeDescriptionLines.append(( mark, line[1:] ))
                            if oldLinesRemaining < 0 or newLinesRemaining < 0:
                                raise InvaildDescription("line %d: too long difference description" % getLineNum(lineIt))
                        line = lineItNext()
                if parseDescriptionLines:
                    changeSink(rangeValues[0], rangeValues[1], changeDescriptionLines)
                rangeValues = None
            else:
                line = lineItNext()
    except StopIteration:
        if oldFile:
            raise InvaildDescription("line %d: missing '+++'" % getLineNum(lineIt))
        if oldLinesRemaining or newLinesRemaining:
            raise InvaildDescription("line %d: unfinished difference description" % getLineNum(lineIt))
        if rangeValues:
            changeSink(rangeValues[0], rangeValues[1], changeDescriptionLines)

if __name__ == '__main__':
    import sys
    import re
    
    newLinePat = re.compile('\r\n?|\n')
    diffFile = sys.argv[1]
    
    s = ''
    if diffFile == '-':
        s = sys.stdin.readlines()
    else:
        with open(diffFile, 'r') as f:
            s = f.read()
    diffLines = newLinePat.split(s)
    
    def file_desk_sink(oldFile, newFile):
        print "--- %s" % oldFile
        print "+++ %s" % newFile
    
    changeRangeHolder = []
    
    def changeSink(oldRange, newRange, markAndLines):
        splittedChanges = split_change(oldRange, newRange, markAndLines)
        for oRange, nRange, mAndL in splittedChanges:
            print "@@@ -%s +%s @@@" % (oRange, nRange)
            lines = [ ("%s%s" % ( m, l )) for m, l in mAndL ]
            print "\n".join(lines)
        
    unified_diff_scanner(diffLines, 
            file_desk_sink, changeSink)
