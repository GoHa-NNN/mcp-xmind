#!/usr/bin/env python3
"""
Image support test: reading embedded images and extracting them to disk.

Covers:
  1. Zen format  -> read returns image {src,width,height} on title-less topics
  2. Zen format  -> extract_images writes topic images + notes HTML images
  3. Legacy format -> read returns image, extract_images writes the file
  4. Broken reference -> reported under "missing", no crash
  5. Re-export -> stale generated files are reclaimed via manifest, user files survive
"""
import json, subprocess, sys, os, tempfile, zipfile

_HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(_HERE, "..", "skills", "xmind", "scripts", "read_xmind.mjs")

# 1x1 transparent PNG
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000154a24f5f0000000049454e44ae"
    "426082"
)

errors = []
passed = 0


def check(ctx, expected, actual, field=""):
    global passed
    if expected != actual:
        errors.append(f"[{ctx}] {field}: expected={expected!r}, got={actual!r}")
    else:
        passed += 1


def run(action):
    p = subprocess.run(
        ["node", SCRIPT], input=json.dumps(action), capture_output=True, text=True
    )
    if p.returncode != 0:
        print(f"NODE ERROR: {p.stderr}")
        sys.exit(1)
    return json.loads(p.stdout)


def make_zen(path):
    content = [{
        "id": "sheet1", "class": "sheet", "title": "S",
        "rootTopic": {
            "id": "root", "class": "topic", "title": "Root",
            "children": {"attached": [
                {"id": "n1", "class": "topic", "title": "",
                 "image": {"src": "xap:resources/pic.png", "width": 100, "height": 50}},
                {"id": "n2", "class": "topic", "title": "Notes",
                 "notes": {"realHTML": {"content": '<p><img src="xap:resources/pic2.png"/></p>'}}},
                {"id": "n3", "class": "topic", "title": "Broken",
                 "image": {"src": "xap:resources/nope.png"}},
            ]},
        },
    }]
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("content.json", json.dumps(content))
        z.writestr("metadata.json", json.dumps({"creator": {"name": "test"}}))
        z.writestr("resources/pic.png", PNG)
        z.writestr("resources/pic2.png", PNG)


def make_zen_small(path):
    """Second version of the map: one image only, so the previous export has stale files."""
    content = [{
        "id": "sheet1", "class": "sheet", "title": "S",
        "rootTopic": {
            "id": "root", "class": "topic", "title": "Root",
            "children": {"attached": [
                {"id": "n1", "class": "topic", "title": "",
                 "image": {"src": "xap:resources/pic.png"}},
            ]},
        },
    }]
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("content.json", json.dumps(content))
        z.writestr("resources/pic.png", PNG)


def make_legacy(path):
    xml = """<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<xmap-content xmlns="urn:xmind:xmap:xmlns:content:2.0"
              xmlns:xhtml="http://www.w3.org/1999/xhtml"
              xmlns:svg="http://www.w3.org/2000/svg" version="2.0">
  <sheet id="s1">
    <topic id="t1">
      <title>Root</title>
      <children>
        <topics type="attached">
          <topic id="t2">
            <title>Pic</title>
            <img xhtml:src="xap:resources/pic.png" svg:width="100" svg:height="50"/>
          </topic>
        </topics>
      </children>
    </topic>
    <title>Sheet</title>
  </sheet>
</xmap-content>"""
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("content.xml", xml)
        z.writestr("meta.xml", "<meta/>")
        z.writestr("resources/pic.png", PNG)


def find_node(sheet, title):
    def walk(n):
        if n.get("title") == title:
            return n
        for c in n.get("children", []):
            hit = walk(c)
            if hit:
                return hit
        return None
    return walk(sheet)


def main():
    tmp = tempfile.mkdtemp(prefix="xmind-image-test-")
    zen = os.path.join(tmp, "img_zen.xmind")
    legacy = os.path.join(tmp, "img_legacy.xmind")
    make_zen(zen)
    make_legacy(legacy)

    # 1. Zen read exposes image metadata on a title-less topic
    root = run({"action": "read", "path": zen})[0]
    n1 = find_node(root, "")
    check("zen read", "xap:resources/pic.png", (n1 or {}).get("image", {}).get("src"), "image.src")
    check("zen read", 100, (n1 or {}).get("image", {}).get("width"), "image.width")

    # 2. Zen extract_images writes topic and notes images
    out = os.path.join(tmp, "zen_out")
    res = run({"action": "extract_images", "path": zen, "outputDir": out})
    check("zen extract", 3, res["totalReferences"], "totalReferences")
    check("zen extract", 2, res["totalFiles"], "totalFiles")
    check("zen extract", 1, len(res.get("missing", [])), "missing")
    check("zen extract", 2, len(res["images"]), "images")
    check("zen extract", True, all(os.path.isfile(i["file"]) for i in res["images"]), "files exist")
    check("zen extract", True, any(i["source"] == "notes" for i in res["images"]), "notes image")
    for i in res["images"]:
        with open(i["file"], "rb") as f:
            check("zen extract", True, f.read() == PNG, "bytes match")
    manifest = os.path.join(out, ".xmind-images.json")
    check("zen extract", True, os.path.isfile(manifest), "manifest written")

    # 2b. Re-export after the map loses an image: stale generated file is reclaimed,
    #     but unrelated user files in the same folder must survive.
    keep = os.path.join(out, "user_keep.txt")
    with open(keep, "w") as f:
        f.write("do not delete")
    small = os.path.join(tmp, "img_zen_small.xmind")
    make_zen_small(small)
    res2 = run({"action": "extract_images", "path": small, "outputDir": out})
    check("zen gc", 1, res2["totalFiles"], "totalFiles")
    check("zen gc", 1, len(res2.get("removedFiles", [])), "removedFiles")
    check("zen gc", False, any(i["file"].endswith("pic2.png") for i in res2["images"]), "stale gone")
    remaining = sorted(os.listdir(out))
    check("zen gc", [".xmind-images.json", "01_pic.png", "user_keep.txt"], remaining, "folder contents")
    check("zen gc", True, os.path.isfile(keep), "user file untouched")

    # 2c. Running again is a no-op for cleanup
    res3 = run({"action": "extract_images", "path": small, "outputDir": out})
    check("zen gc", 0, len(res3.get("removedFiles", [])), "no repeat deletions")

    # 2d. clean:false disables deletion
    res4 = run({"action": "extract_images", "path": small, "outputDir": out, "clean": False})
    check("zen gc", 0, len(res4.get("removedFiles", [])), "clean=false")

    # 3. Legacy read + extract
    lroot = run({"action": "read", "path": legacy})[0]
    lp = find_node(lroot, "Pic")
    check("legacy read", "xap:resources/pic.png", (lp or {}).get("image", {}).get("src"), "image.src")
    check("legacy read", "100", (lp or {}).get("image", {}).get("width"), "image.width")

    lout = os.path.join(tmp, "legacy_out")
    lres = run({"action": "extract_images", "path": legacy, "outputDir": lout})
    check("legacy extract", 1, lres["totalFiles"], "totalFiles")
    check("legacy extract", True, os.path.isfile(lres["images"][0]["file"]), "file exists")

    total = passed + len(errors)
    print(f"RESULT: {passed}/{total} passed")
    for e in errors:
        print("  x " + e)
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
