import itertools

class DollyTuple(object):
    __slots__ = ( '__item', '__length' )
    
    def __init__(self, item, length):
        assert length > 0
        self.__item = item
        self.__length = length
    
    @staticmethod
    def try_convert(seq):
        len_seq = len(seq)
        if not len_seq:
            return seq
        item0 = seq[0]
        for item in seq[1:]:
            if item != item0:
                return seq
        return DollyTuple(item0, len_seq)
    
    def __len__(self): return self.__length
    
    def __getitem__(self, indexOrSlice):
        if isinstance(indexOrSlice, int):
            return self.__item
        assert indexOrSlice.step is None
        if indexOrSlice.start is None:
            if indexOrSlice.stop is None:
                return self
            else:
                run = indexOrSlice.stop
                if run < 0:
                    run += self.__length
        else:
            start = indexOrSlice.start
            if start < 0: 
                start += self.__length
            if indexOrSlice.stop is None:
                run = self.__length - start
            else:
                stop = indexOrSlice.stop
                if stop < 0:
                    stop += self.__length
                run = stop - start
        if run <= 0:
            return ()
        elif run < self.__length:
            return DollyTuple(self.__item, run)
        elif run == self.__length:
            return self
        else:
            raise IndexError
    
    def __iter__(self): return itertools.repeat(self.__item, self.__length)
    
    def __contains__(self, item): return item == self.__item
    
    def __repr__(self): return "DollyTuple(%s,%s)" % ( repr(self.__item), repr(self.__length) )

def __test():
    a = [ None ] * 100
    b = DollyTuple(None, 100)
    assert a[0] == b[0]
    for ai, bi in zip(a, b):
        assert ai == bi
    
    assert len(a) == len(b)
    assert tuple(a[0:10]) == tuple(b[0:10])
    assert tuple(a[-3:1]) == tuple(b[-3:1])
    
    print "tests passed."

if __name__ == '__main__': __test()