"""Package reviewed source files, never workspace state or private uploads."""
import hashlib
import json
from pathlib import Path
import zipfile

ROOT=Path(__file__).resolve().parents[1]
TOP={'.gitignore','.dockerignore','.env.example','Dockerfile.sandbox','pyproject.toml','requirements.lock','README.md'}
EXTENSIONS={'backend':{'.py','.json'},'frontend':{'.html'},'scripts':{'.py','.cjs','.ps1','.sh'},'tests':{'.py'},'docs':{'.md','.json','.yaml','.png'},'samples':{'.csv','.json','.md'},'.github':{'.yml','.yaml'}}


def source_files():
    paths=[ROOT/name for name in TOP]
    for folder,extensions in EXTENSIONS.items():
        paths.extend(p for p in (ROOT/folder).rglob('*') if p.is_file() and p.suffix in extensions and '__pycache__' not in p.parts and not p.is_symlink())
    return sorted(paths,key=lambda p:p.relative_to(ROOT).as_posix())


def main():
    files=source_files()
    output=ROOT/'dist';output.mkdir(exist_ok=True)
    archive=output/'hacknex-github-ready.zip'
    manifest={}
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as bundle:
        for source in files:
            relative=source.relative_to(ROOT).as_posix()
            content=source.read_bytes()
            manifest[relative]=hashlib.sha256(content).hexdigest()
            bundle.writestr('hacknex/'+relative,content)
        bundle.writestr('hacknex/PACKAGE-MANIFEST.json',json.dumps({'files':manifest},indent=2)+'\n')
    with zipfile.ZipFile(archive) as bundle:
        assert bundle.testzip() is None
        assert not any('/.git/' in n or '/.venv/' in n or '/data/' in n or '/.test-env/' in n or n.endswith('/.env') for n in bundle.namelist())
        for relative,expected in manifest.items():
            assert hashlib.sha256(bundle.read('hacknex/'+relative)).hexdigest()==expected
    checksum=hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix('.zip.sha256').write_text(checksum+'  '+archive.name+'\n',encoding='utf-8')
    print(f'{archive}\n{len(files)} source files; {archive.stat().st_size:,} bytes; SHA-256 {checksum}')


if __name__=='__main__':main()
