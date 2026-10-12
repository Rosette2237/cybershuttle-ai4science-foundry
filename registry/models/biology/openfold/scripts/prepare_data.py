#!/usr/bin/env python
"""Stage OpenFold inference inputs under $OF_DATA and write results/data_manifest.tsv.

Targets: CAMEO 3D targets of the 13 weekly releases 2021-10-23..2022-01-15 (OpenFold's validation
window, "three months ending January 16, 2022"), <= 700 residues. The live CAMEO 3D API is offline
and today's PDB obsolete list has a self-referencing entry that crashes OpenFold, so both come from
checksummed Wayback snapshots of the period. Alignments and template hits come from
OpenProteinSet; chains without them are recorded as excluded. The bundled 6KWC example is the
smoke test. Every target is featurized with OpenFold's own data pipeline, downloading each template
mmCIF the featurizer asks for, so inference reads exactly the files fetched here.
"""
import csv
import hashlib
import json
import os
import shutil
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from openfold.config import model_config
from openfold.data import data_pipeline, mmcif_parsing, parsers, templates

DATA = Path(os.environ["OF_DATA"])
REPO = Path(os.environ["OF_REPO"])
RESULTS = Path(__file__).resolve().parents[1] / "results"
S3 = "https://openfold.s3.amazonaws.com"
RCSB = "https://files.rcsb.org/download"
WAYBACK = "https://web.archive.org/web"
ARCHIVED = {  # name: (snapshot URL, sha256)
    "cameo_targets_1-year_to_2022-01-15.json": (
        f"{WAYBACK}/20230807161458id_/https://cameo3d.org/modeling/targets/1-year/ajax/?to_date=2022-01-15",
        "b7b680a09536de3f4cdb5239d800e66fe22b668d0f5b7df63441cc9c9a195572"),
    "obsolete.dat": (
        f"{WAYBACK}/20220621163601id_/https://files.rcsb.org/pub/pdb/data/status/obsolete.dat",
        "8f14c08dd4d77fadf538bc15f561a5c3e1fb7d8eb8114a47d490b0eccb3e8b54"),
}
WINDOW = ("2021-10-23", "2022-01-15")
MAX_LEN = 700


def fetch(url, path, tries=3):
    path = Path(path)
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    part = Path(f"{path}.{threading.get_ident()}.part")
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=300) as r, open(part, "wb") as f:
                shutil.copyfileobj(r, f)
            os.replace(part, path)
            return path
        except OSError:
            if attempt == tries - 1:
                raise
            time.sleep(5 * (attempt + 1))


def sha256(paths):
    h = hashlib.sha256()
    for p in sorted(paths):
        h.update(Path(p).read_bytes())
    return h.hexdigest()


def s3_keys(prefix):
    xml = urllib.request.urlopen(f"{S3}/?list-type=2&prefix={prefix}", timeout=60).read().decode()
    return [k.split("</Key>")[0] for k in xml.split("<Key>")[1:]]


def stage(t, duplicates, pipeline):
    tag, aln = t["tag"], DATA / "alignments" / t["tag"]
    cif = fetch(f"{RCSB}/{t['pdb'].upper()}.cif", DATA / "mmcif" / f"{t['pdb']}.cif")
    mmcif = mmcif_parsing.parse(file_id=t["pdb"], mmcif_string=cif.read_text()).mmcif_object
    seq = mmcif.chain_to_seqres[t["chain"]]
    t["length"] = len(seq)
    if len(seq) > MAX_LEN:
        return {**t, "status": f"excluded: > {MAX_LEN} residues"}

    if t["set"] == "smoke":
        shutil.copytree(REPO / "examples/monomer/alignments" / tag, aln, dirs_exist_ok=True)
        t["msa_source"] = f"openfold/examples/monomer/alignments/{tag}"
    else:
        # OpenProteinSet keeps one alignment per unique sequence; duplicates map to a representative
        for chain in [tag] + [c for c in duplicates.get(tag, []) if c != tag]:
            keys = s3_keys(f"pdb/{chain}/")
            if keys:
                break
        else:
            return {**t, "status": "excluded: no OpenProteinSet alignment"}
        for key in keys:
            fetch(f"{S3}/{key}", aln / Path(key).name)
        t["msa_source"] = f"s3://openfold/pdb/{chain}/"
    if {parsers.parse_a3m(p.read_text()).sequences[0] for p in aln.glob("*.a3m")} != {seq}:
        return {**t, "status": "excluded: alignment query differs from SEQRES"}

    fasta = DATA / "fasta" / t["set"] / f"{tag}.fasta"
    fasta.parent.mkdir(parents=True, exist_ok=True)
    fasta.write_text(f">{tag}\n{seq}\n")
    while True:  # dry-run featurization; fetch template mmCIFs on demand
        try:
            feats = pipeline.process_fasta(fasta_path=str(fasta), alignment_dir=str(aln))
            break
        except FileNotFoundError as e:
            if not str(e.filename).endswith(".cif"):
                raise
            fetch(f"{RCSB}/{Path(e.filename).stem.upper()}.cif", e.filename)
    names = [n.decode() for n in feats["template_domain_names"]]
    assert all(n[:4] != t["pdb"] for n in names), f"{tag} selected itself as a template"
    return {**t, "status": "ok", "mmcif_sha256": sha256([cif]), "msa_sha256": sha256(aln.iterdir()),
            "n_msa_seqs": int(feats["num_alignments"][0]), "templates": " ".join(names)}


def main():
    for name, (url, digest) in ARCHIVED.items():
        assert sha256([fetch(url, DATA / name)]) == digest, f"{name} does not match its checksum"
    for name, url in {"duplicate_pdb_chains.txt": f"{S3}/duplicate_pdb_chains.txt",
                      "mmcif_cache.json": f"{S3}/data_caches/mmcif_cache.json"}.items():  # template release dates
        print(sha256([fetch(url, DATA / name)]), name)
    cameo = DATA / "cameo_targets_1-year_to_2022-01-15.json"
    duplicates = {c: g for g in (line.split() for line in open(DATA / "duplicate_pdb_chains.txt")) for c in g}

    targets = [{"set": "smoke", "tag": "6KWC_1", "pdb": "6kwc", "chain": "A", "cameo_date": ""}]
    targets += [
        {"set": "cameo", "tag": f"{t['pdbid']}_{t['pdbid_chain']}", "pdb": t["pdbid"],
         "chain": t["pdbid_chain"], "cameo_date": t["date"]}
        for t in json.loads(cameo.read_text())["aaData"] if WINDOW[0] <= t["date"] <= WINDOW[1]
    ]

    def prefetch(t):  # failures are recorded when the target is staged
        try:
            fetch(f"{RCSB}/{t['pdb'].upper()}.cif", DATA / "mmcif" / f"{t['pdb']}.cif")
        except OSError:
            pass

    # Ground-truth mmCIFs first: they share the template directory, which must not be empty.
    # Targets are never used as their own templates: all were released after MAX_TEMPLATE_DATE.
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(prefetch, targets))
    featurizer = templates.HhsearchHitFeaturizer(  # same settings run_pretrained_openfold.py uses
        mmcif_dir=str(DATA / "mmcif"),
        max_template_date=os.environ["MAX_TEMPLATE_DATE"],
        max_hits=model_config("model_1_ptm").data.predict.max_templates,
        kalign_binary_path=shutil.which("kalign"),
        release_dates_path=str(DATA / "mmcif_cache.json"),
        obsolete_pdbs_path=str(DATA / "obsolete.dat"),
    )
    pipeline = data_pipeline.DataPipeline(template_featurizer=featurizer)

    def safe_stage(t):
        try:
            return stage(t, duplicates, pipeline)
        except Exception as e:  # keep failed targets out of the inference set
            (DATA / "fasta" / t["set"] / f"{t['tag']}.fasta").unlink(missing_ok=True)
            return {**t, "status": f"excluded: {type(e).__name__}: {e}"}

    shutil.rmtree(DATA / "fasta", ignore_errors=True)
    with ThreadPoolExecutor(8) as pool:
        rows = list(pool.map(safe_stage, targets))

    cols = ["set", "tag", "pdb", "chain", "cameo_date", "length", "status",
            "mmcif_sha256", "msa_source", "msa_sha256", "n_msa_seqs", "templates"]
    with open(RESULTS / "data_manifest.tsv", "w", newline="") as f:
        writer = csv.DictWriter(f, cols, delimiter="\t", restval="", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    for status in sorted({r["status"] for r in rows}):
        print(sum(r["status"] == status for r in rows), status)


if __name__ == "__main__":
    main()
