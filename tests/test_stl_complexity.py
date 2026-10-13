import struct
from pathlib import Path
import tempfile
import unittest
from app.project_builder import inspect_stl

class StlComplexityTests(unittest.TestCase):
    def setUp(self): self.tmp=tempfile.TemporaryDirectory(); self.path=Path(self.tmp.name)/'shape.stl'
    def tearDown(self): self.tmp.cleanup()
    def binary(self,count):
        triangle=struct.pack('<12fH',0,0,1,0,0,0,1,0,1,0,1,0,0)
        self.path.write_bytes(b'bounded fixture'.ljust(80,b'\0')+struct.pack('<I',count)+triangle*count)
    def ascii(self,count):
        self.path.write_text('solid fixture\n'+(' facet normal 0 0 1\n outer loop\n vertex 0 0 0\n vertex 1 0 1\n vertex 0 1 0\n endloop\n endfacet\n'*count)+'endsolid fixture\n')
    def test_binary_within_bounds_matches_previous_inspection(self):
        self.binary(2); self.assertEqual(inspect_stl(self.path),inspect_stl(self.path,max_triangles=2,max_line_bytes=4096))
    def test_binary_excess_triangle_rejected(self):
        self.binary(2)
        with self.assertRaisesRegex(ValueError,'complexity'): inspect_stl(self.path,max_triangles=1,max_line_bytes=4096)
    def test_ascii_within_bounds_matches_previous_inspection(self):
        self.ascii(2); self.assertEqual(inspect_stl(self.path),inspect_stl(self.path,max_triangles=2,max_line_bytes=4096))
    def test_ascii_excess_triangle_rejected(self):
        self.ascii(2)
        with self.assertRaisesRegex(ValueError,'complexity'): inspect_stl(self.path,max_triangles=1,max_line_bytes=4096)
    def test_ascii_long_line_rejected(self):
        self.ascii(1); self.path.write_text('solid '+('x'*5000)+'\n'+self.path.read_text())
        with self.assertRaisesRegex(ValueError,'oversized'): inspect_stl(self.path,max_triangles=10,max_line_bytes=4096)
    def test_invalid_utf8_ascii_rejected(self):
        self.ascii(1); self.path.write_bytes(b'\xff\n'+self.path.read_bytes())
        with self.assertRaises(ValueError): inspect_stl(self.path,max_triangles=10,max_line_bytes=4096)
    def test_malformed_binary_never_trusted_from_header_alone(self):
        self.binary(1); content=self.path.read_bytes(); self.path.write_bytes(content[:80]+struct.pack('<I',999999999)+content[84:])
        with self.assertRaises(ValueError): inspect_stl(self.path,max_triangles=10,max_line_bytes=4096)
    def test_nonfinite_vertices_remain_rejected(self):
        self.ascii(1); self.path.write_text(self.path.read_text().replace('vertex 1 0 1','vertex 1e400 0 1'))
        with self.assertRaises(ValueError): inspect_stl(self.path,max_triangles=10,max_line_bytes=4096)

if __name__=='__main__': unittest.main()
