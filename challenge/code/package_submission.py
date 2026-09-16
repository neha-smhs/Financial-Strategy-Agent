"""Package only participant inputs, solution sources, and final evaluation."""
from pathlib import Path
import argparse
import shutil
import zipfile

def package(destination):
    root=Path(__file__).resolve().parent.parent;dest=Path(destination).resolve();dest.mkdir(parents=True,exist_ok=True)
    top=['README.md','STARTER_README.md','AGENTS.md','problem_statement.md','requirements.txt','.env.example','.gitignore','output.csv']
    files=[root/name for name in top if (root/name).is_file()]
    files+=list((root/'code').glob('*.py'))
    files+=[p for p in (root/'dataset').rglob('*') if p.is_file() and p.suffix in {'.csv','.png'}]
    files+=[root/'evaluation'/name for name in ['usage_report.md','ASSUMPTIONS.md','output_validation.json','test_results.txt','summary.md','comparison.md','comparison.json','reproducibility.json'] if (root/'evaluation'/name).exists()]
    for directory in ['final','public_samples','baseline']:
        files += [p for p in (root/'evaluation'/directory).rglob('*') if p.is_file() and 'traces' not in p.parts and p.suffix in {'.json','.md','.csv','.txt','.py'}]
    with zipfile.ZipFile(dest/'code.zip','w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for p in sorted(set(files)):z.write(p,p.relative_to(root))
    shutil.copy2(root/'output.csv',dest/'output.csv')
    shutil.copy2(root/'log.txt',dest/'chat_transcript.txt')
    shutil.copy2(root/'evaluation'/'summary.md',dest/'evaluation_report.md')
    print('Packaged:',', '.join(p.name for p in sorted(dest.iterdir()) if p.is_file()))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--destination',default='../deliverables');package(p.parse_args().destination)
