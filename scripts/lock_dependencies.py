"""Snapshot the installed, tested dependency closure, excluding comparison tools."""
import importlib.metadata as metadata
from pathlib import Path
from packaging.requirements import Requirement
import tomllib
ROOT=Path(__file__).resolve().parents[1]
project=tomllib.loads((ROOT/'pyproject.toml').read_text())['project']

def closure(requirements):
    seen={}
    def include(name):
        distribution=metadata.distribution(name);key=distribution.metadata['Name']
        if key in seen:return
        seen[key]=distribution.version
        for spec in distribution.requires or []:
            requirement=Requirement(spec)
            if requirement.marker is None or requirement.marker.evaluate({'extra':''}):include(requirement.name)
    for spec in requirements:include(Requirement(spec).name)
    return ''.join(f'{name}=={version}\n' for name,version in sorted(seen.items(),key=lambda pair:pair[0].lower()))

if __name__=='__main__':
    (ROOT/'requirements-runtime.lock').write_text(closure(project['dependencies']))
    (ROOT/'requirements-dev.lock').write_text(closure(project['dependencies']+project['optional-dependencies']['test']+project['optional-dependencies']['fixtures']))
    print('Runtime and development locks saved from installed distributions.')
