"""Compatibility entry point for public-sample evaluation."""
from pathlib import Path
import runpy,sys
code=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(code))
runpy.run_path(str(code/"evaluate.py"),run_name="__main__")
