import unittest
import tempfile
import os
from os.path import join

from numpy import logaddexp
import cpmpy as cp
from cpmpy.expressions.variables import NullShapeError, _IntVarImpl, _BoolVarImpl, NegBoolView, NDVarArray


class TestModel(unittest.TestCase):
    
    def setUp(self) -> None:
        self.tempdir = tempfile.mkdtemp()
        print(self.tempdir)
        return super().setUp()
    
    def tearDown(self) -> None:
        os.rmdir(self.tempdir)
        return super().tearDown()

    def test_ndarray(self):
        iv = cp.intvar(1,9, shape=3)
        m = cp.Model( iv > 3 )
        m += (iv[0] == 5)
        self.assertTrue(m.solve())

    def test_empty(self):
        m = cp.Model()
        m += [] # should do nothing
        assert(len(m.constraints) == 0)

    def test_io_nempty(self):
        fname = join(self.tempdir, "model")
        iv = cp.intvar(1,9, shape=3)
        m = cp.Model( iv > 3 )
        m += (iv[0] == 5)
        m.to_file(fname)

        loaded = cp.Model.from_file(fname)
        self.assertTrue(loaded.solve())
        os.remove(fname)

    def test_copy(self):
        x,y,z = [cp.boolvar(name=n) for n in "xyz"]

        cons1 = x > y
        cons2 = x + y == 1
        m = cp.Model([cons1, cons2])

        memodict = dict()
        m_dcopy = m.copy()
        print(memodict)
        m_dcopy.solve()

        self.assertTrue(cons1.value())
        self.assertTrue(cons2.value())

        m.solve()

        m2 = m.copy()

        self.assertTrue(m2.constraints[0].value())
        self.assertTrue(m2.constraints[1].value())


    def test_deepcopy(self):
        x,y,z = [cp.boolvar(name=n) for n in "xyz"]

        cons1 = x > y
        cons2 = x + y == 1
        m = cp.Model([cons1, cons2])

        memodict = dict()
        m_dcopy = m.deepcopy(memodict)
        print(memodict)
        m_dcopy.solve()

        self.assertIsNone(cons1.value())
        self.assertIsNone(cons2.value())

        m.solve()

        m2 = m.deepcopy()

        self.assertTrue(m2.constraints[0].value())
        self.assertTrue(m2.constraints[1].value())

