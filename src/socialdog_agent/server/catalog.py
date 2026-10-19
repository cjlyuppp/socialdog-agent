"""Prepare verified physical cutoffs; jobs keep their original immutable package."""
from datetime import date
import json
from pathlib import Path
import shutil
import tempfile
import threading

import historical_backtest as history

from .data import AgentData
from .store import APIError


class DataCatalog:
    def __init__(self, source, store):
        self.base, self.store = source, store
        self.root = store.path.parent / "data-versions"
        self.root.mkdir(exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        self.lock = threading.Lock()
        self.cache = {source.cutoff: source}
        manifest = history.read_package(source.package)
        self.fingerprint = manifest["source_sha256"]
        self.archive = None
        self.archive_description = None
        # Only sibling packages with the exact same original workbook are eligible.
        for candidate in sorted(source.package.parent.glob("*/manifest.json")):
            try:
                metadata = json.loads(candidate.read_text())
                if metadata.get("source_sha256") != self.fingerprint or metadata["data_description"].get("as_of"):
                    continue
                verified = history.read_package(candidate.parent)
                raw = candidate.parent / "原始文件_字节一致备份.xlsx"
                if not raw.is_file() or history.digest(raw) != self.fingerprint:
                    continue
                self.archive, self.archive_description = raw, verified["data_description"]
                break
            except (ValueError, OSError, KeyError):
                continue

    @property
    def cutoff(self):
        return self.store.settings()["cutoff_date"]

    @property
    def description(self):
        return self.prepare(self.cutoff).description

    def bounds(self):
        description = self.archive_description or self.base.description
        return {"can_change_cutoff": self.archive is not None,
                "earliest_date": description["observation_start"][:10],
                "latest_date": description["observation_end"][:10]}

    def prepare(self, cutoff):
        cutoff = date.fromisoformat(cutoff).isoformat()
        with self.lock:
            if cutoff in self.cache:
                return self.cache[cutoff]
            target = self.root / cutoff
            if not target.exists():
                if not self.archive:
                    raise APIError(409, "尚未安装同源完整数据包，暂不能修改 T。请联系部署人员。")
                bounds = self.bounds()
                if not bounds["earliest_date"] <= cutoff <= bounds["latest_date"]:
                    raise APIError(400, "T 必须在已导入数据的日期范围内。")
                staging = Path(tempfile.mkdtemp(prefix=".prepare-", dir=self.root))
                try:
                    history.build(self.archive, staging / "package", as_of=cutoff,
                                  sheet_name=self.archive_description["source_sheet"], report=False)
                    if history.read_package(staging / "package")["source_sha256"] != self.fingerprint:
                        raise APIError(409, "原始数据已变化，请先核验后再发布。")
                    AgentData(staging / "package")
                    (staging / "package").rename(target)
                finally:
                    shutil.rmtree(staging)
            verified = history.read_package(target)
            if verified["source_sha256"] != self.fingerprint:
                raise APIError(409, "截断数据与原始数据来源不一致，请联系部署人员。")
            self.cache[cutoff] = AgentData(target)
            return self.cache[cutoff]

    def for_version(self, version):
        if "_asof" not in version:
            raise APIError(409, "题库的数据版本不可用。")
        source = self.prepare(version.rsplit("_asof", 1)[1])
        if source.description["dataset_version"] != version:
            raise APIError(409, "题库对应的原始数据尚未加载。")
        return source

    def tools(self, state, config):
        return self.for_version(config["dataset_version"]).tools(state, config)

    def status(self, settings, model):
        return self.prepare(settings["cutoff_date"]).status(settings, model)
