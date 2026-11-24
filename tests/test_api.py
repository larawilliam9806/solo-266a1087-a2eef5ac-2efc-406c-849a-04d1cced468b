"""End-to-end HTTP tests for import / assign / decode and compatibility."""

import pytest


JIA = {
    "name": "甲厂",
    "marker": "1",
    "prefix": "10",
    "scale": {"start": 2, "length": 2},
    "weight": {"start": 4, "length": 4},
}
YI = {
    "name": "乙厂",
    "marker": "2",
    "prefix": "20",
    "scale_start": 2,
    "scale_length": 3,
    "weight_start": 5,
    "weight_length": 3,
}


def import_two(client):
    r = client.post("/api/import", json={"templates": [JIA, YI]})
    assert r.status_code == 200, r.text
    return r.json()


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_root_index(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "endpoints" in r.json()


def test_import_preserves_markers(client):
    data = import_two(client)
    assert data["valid"] is True
    cfg = client.get("/api/config").json()
    assert cfg["used_markers"] == ["1", "2"]
    assert [t["marker"] for t in cfg["templates"]] == ["1", "2"]
    # integer alias is exposed too
    assert cfg["marker_digits"] == [1, 2]


def test_import_bare_list_and_aligned_markers(client):
    r = client.post(
        "/api/templates/import",
        json=[
            {"name": "a", "prefix": "1", "scale": [2, 4], "weight": [4, 8]},
        ],
    )
    # bare list without markers must fail (import preserves given markers)
    assert r.status_code == 422
    r = client.post(
        "/api/import",
        json={
            "templates": [
                {"name": "a", "prefix": "1", "scale": [2, 4], "weight": [4, 8]}
            ],
            "markers": ["5"],
        },
    )
    assert r.status_code == 200, r.text
    assert client.get("/api/config").json()["used_markers"] == ["5"]


def test_decode_happy_path_preserves_leading_zeros(client):
    import_two(client)
    # marker 1, prefix 10, scale body[2:4] = "07", weight body[4:8] = "0123"
    code = "1" + "10" + "07" + "0123"
    r = client.post("/api/decode", json={"code": code})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["marker"] == "1"
    assert j["body"] == "10070123"
    assert j["scale_no"] == "07"  # leading zero preserved, string
    assert isinstance(j["scale_no"], str)
    assert j["net_weight_g"] == 123
    assert isinstance(j["net_weight_g"], int)
    # chinese keys
    assert j["秤号"] == "07"
    assert j["净重克数"] == 123


def test_decode_get_and_path_aliases(client):
    import_two(client)
    code = "110070123"
    assert client.get(f"/api/decode/{code}").json()["scale_no"] == "07"
    assert client.get("/api/decode", params={"code": code}).json()["scale_no"] == "07"
    assert client.post("/decode", json={"code": code}).status_code == 200
    assert client.post("/api/jiema", json={"code": code}).status_code == 200


def test_decode_unknown_marker_404(client):
    import_two(client)
    r = client.post("/api/decode", json={"code": "910070123"})
    assert r.status_code == 404
    assert r.json()["code"] == "NO_MATCHING_TEMPLATE"


def test_decode_invalid_length(client):
    import_two(client)
    r = client.post("/api/decode", json={"code": "123"})
    assert r.status_code == 422
    r = client.post("/api/decode", json={"code": "12345678"})
    assert r.status_code == 422  # 8-digit string asks for explicit 0 marker


def test_assign_reuses_old_marker_with_zero_new_digits(client):
    import_two(client)
    # Same extraction as 甲 and disjoint-ish prefix extension -> same decode
    t = {
        "name": "丙厂",
        "prefix": "109",
        "scale": {"start": 2, "length": 2},
        "weight": {"start": 4, "length": 4},
    }
    r = client.post("/api/assign", json={"templates": [t]})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["markers"] == ["1"]
    assert j["new_digit_count"] == 0
    assert j["new_digits"] == []
    assert j["assignments"][0]["marker_digit"] == 1
    # persisted
    cfg = client.get("/api/config").json()
    assert cfg["template_count"] == 3


def test_assign_introduces_minimum_fresh_digit(client):
    import_two(client)
    # conflicts with both marker-1 and marker-2 templates: empty prefix and
    # a scale length different from both (2 and 3) -> length 1 conflicts.
    t = {
        "name": "通配",
        "prefix": "",
        "scale": {"start": 0, "length": 1},
        "weight": {"start": 0, "length": 1},
    }
    r = client.post("/api/assign", json={"templates": [t]})
    assert r.status_code == 200, r.text
    j = r.json()
    assert len(j["markers"]) == 1
    m = j["markers"][0]
    assert m not in ("1", "2")
    assert j["new_digit_count"] == 1
    assert j["new_digits"] == [m]
    # smallest available fresh digit is 0
    assert m == "0"


def test_batch_markers_aligned_and_valid(client):
    import_two(client)
    batch = [
        {
            "name": f"n{i}",
            "prefix": f"5{i}",
            "scale": {"start": 2, "length": 2},
            "weight": {"start": 4, "length": 4},
        }
        for i in range(3)
    ]
    r = client.post("/api/templates/batch", json={"templates": batch})
    assert r.status_code == 200, r.text
    j = r.json()
    assert len(j["markers"]) == 3
    assert len(j["assignments"]) == 3
    # every assignment carries the marker at its aligned index
    for i, a in enumerate(j["assignments"]):
        assert a["marker"] == j["markers"][i]
        assert a["index"] == i


def test_total_limit_12_and_atomic_failure(client):
    # import 10 templates
    ten = [
        {
            "name": f"f{i}",
            "marker": str(i),
            "prefix": str(i),
            "scale": {"start": 2, "length": 2},
            "weight": {"start": 4, "length": 4},
        }
        for i in range(10)
    ]
    r = client.post("/api/import", json={"templates": ten})
    assert r.status_code == 200
    v1 = client.get("/api/config").json()["version"]

    two = [
        {
            "name": "a",
            "prefix": "81",
            "scale": {"start": 2, "length": 2},
            "weight": {"start": 4, "length": 4},
        },
        {
            "name": "b",
            "prefix": "82",
            "scale": {"start": 2, "length": 2},
            "weight": {"start": 4, "length": 4},
        },
    ]
    r = client.post("/api/assign", json={"templates": two})
    assert r.status_code == 200
    assert client.get("/api/config").json()["template_count"] == 12

    one_more = [
        {
            "name": "c",
            "prefix": "83",
            "scale": {"start": 2, "length": 2},
            "weight": {"start": 4, "length": 4},
        }
    ]
    r = client.post("/api/assign", json={"templates": one_more})
    assert r.status_code == 409
    assert r.json()["code"] == "TEMPLATE_LIMIT_EXCEEDED"
    cfg = client.get("/api/config").json()
    assert cfg["template_count"] == 12
    assert cfg["version"] != v1  # successful batch did move version


def test_no_solution_keeps_original_config(client):
    ten = [
        {
            "name": f"f{i}",
            "marker": str(i),
            "prefix": str(i),
            "scale": {"start": 2, "length": 2},
            "weight": {"start": 4, "length": 4},
        }
        for i in range(10)
    ]
    client.post("/api/import", json={"templates": ten})
    before = client.get("/api/config").json()

    blocked = [
        {
            "name": "x",
            "prefix": "",
            "scale": {"start": 0, "length": 1},
            "weight": {"start": 0, "length": 1},
        }
    ]
    r = client.post("/api/assign", json={"templates": blocked})
    assert r.status_code == 409
    assert r.json()["code"] == "NO_SOLUTION"
    after = client.get("/api/config").json()
    assert after["version"] == before["version"]
    assert after["template_count"] == 10


def test_save_failure_rolls_back(client, monkeypatch):
    import_two(client)
    before = client.get("/api/config").json()

    from app.main import storage as st

    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(st, "commit_new_config", boom)
    r = client.post(
        "/api/assign",
        json={
            "templates": [
                {
                    "name": "x",
                    "prefix": "33",
                    "scale": {"start": 2, "length": 2},
                    "weight": {"start": 4, "length": 4},
                }
            ]
        },
    )
    assert r.status_code == 500
    assert r.json()["code"] == "SAVE_FAILED"
    after = client.get("/api/config").json()
    assert after["version"] == before["version"]
    assert after["template_count"] == before["template_count"]


def test_lenient_camelcase_and_array_ranges(client):
    r = client.post(
        "/api/assign",
        json={
            "templates": [
                {
                    "templateName": "灵活",
                    "bodyPrefix": "77",
                    "scaleRange": [2, 4],
                    "weightRange": [4, 8],
                }
            ]
        },
    )
    assert r.status_code == 200, r.text
    m = r.json()["markers"][0]
    code = m + "77" + "05" + "0099"
    d = client.post("/api/decode", json={"billCode": code}).json()
    assert d["scale_no"] == "05"
    assert d["net_weight_g"] == 99


def test_one_based_index_base(client):
    # 1-based inclusive-ish start+length: scale starts at position 3 (1-based)
    # => zero start 2, length 2.
    r = client.post(
        "/api/assign",
        json={
            "index_base": 1,
            "templates": [
                {
                    "name": "one",
                    "prefix": "10",
                    "scale_start": 3,
                    "scale_length": 2,
                    "weight_start": 5,
                    "weight_length": 4,
                }
            ],
        },
    )
    assert r.status_code == 200, r.text
    t = client.get("/api/config").json()["templates"][0]
    assert t["scale_start"] == 2
    assert t["weight_start"] == 4


def test_invalid_range_rejected(client):
    r = client.post(
        "/api/assign",
        json={
            "templates": [
                {
                    "name": "bad",
                    "prefix": "1",
                    "scale": {"start": 7, "length": 3},
                    "weight": {"start": 4, "length": 2},
                }
            ]
        },
    )
    assert r.status_code == 422
    assert client.get("/api/config").json()["template_count"] == 0


def test_reset_clears_config(client):
    import_two(client)
    r = client.post("/api/admin/reset")
    assert r.status_code == 200
    assert client.get("/api/config").json()["template_count"] == 0


def test_openapi_available(client):
    r = client.get("/openapi.json")
    assert r.status_code == 200
    assert "paths" in r.json()


def test_chinese_field_names(client):
    r = client.post(
        "/api/import",
        json={
            "模板": [
                {
                    "名称": "中方",
                    "标记": 3,
                    "正文前缀": "66",
                    "秤号": {"起点": 2, "长度": 2},
                    "净重克数": {"起点": 4, "长度": 4},
                }
            ]
        },
    )
    assert r.status_code == 200, r.text
    code = "3" + "66" + "08" + "0050"
    d = client.post("/api/decode", json={"磅单码": code}).json()
    assert d["scale_no"] == "08"
    assert d["net_weight_g"] == 50


def test_integer_marker_input_normalized(client):
    r = client.post(
        "/api/import",
        json={
            "templates": [
                {
                    "name": "zero",
                    "marker": 0,
                    "prefix": "1",
                    "scale": {"start": 2, "length": 1},
                    "weight": {"start": 4, "length": 2},
                }
            ]
        },
    )
    assert r.status_code == 200
    assert client.get("/api/config").json()["used_markers"] == ["0"]
    # "0" marker + body "12345678"; as a JSON number the leading 0 is lost
    # (12345678) and the server left-pads back to 9 digits.
    d = client.post("/api/decode", json={"code": 12345678}).json()
    assert d["marker"] == "0"
    assert d["body"] == "12345678"
    assert d["scale_no"] == "3"
    assert d["net_weight_g"] == 56


def test_encode_roundtrip(client):
    import_two(client)
    r = client.post(
        "/api/encode",
        json={
            "marker": "1",
            "index": 0,
            "scale_no": "7",
            "net_weight_g": 123,
        },
    )
    assert r.status_code == 200, r.text
    code = r.json()["code"]
    assert code == "110070123"
    d = client.post("/api/decode", json={"code": code}).json()
    assert d["scale_no"] == "07"
    assert d["net_weight_g"] == 123


def test_persistence_across_storage_instances(tmp_path):
    from app.storage import Storage

    db = str(tmp_path / "p.db")
    s1 = Storage(db)
    from app.domain import Range, TemplateSpec

    s1.commit_new_config(
        [
            TemplateSpec(
                name="x",
                marker="4",
                prefix="9",
                scale=Range(1, 2),
                weight=Range(3, 3),
            )
        ],
        reason="t",
    )
    s1.close()
    s2 = Storage(db)
    version, templates = s2.active_config()
    assert version == 1 and len(templates) == 1
    assert templates[0].marker == "4"
    s2.close()


def test_many_alias_paths(client):
    payload = {
        "templates": [
            {"name": "a", "prefix": "4", "scale": [2, 4], "weight": [4, 8]}
        ]
    }
    for path in ["/api/peima", "/peima", "/api/register", "/api/markers"]:
        client.post("/api/admin/reset")
        r = client.post(path, json=payload)
        assert r.status_code == 200, (path, r.status_code, r.text)
        assert r.json()["markers"] == ["0"]


def test_shared_marker_agreeing_templates_both_match(client):
    # prefix "12" and "123" can share body "12345007"; identical ranges =>
    # same decode, so sharing marker 5 is legal and decode returns the value.
    client.post(
        "/api/import",
        json={
            "templates": [
                {"name": "a", "marker": "5", "prefix": "12",
                 "scale": {"start": 2, "length": 2},
                 "weight": {"start": 4, "length": 4}},
                {"name": "b", "marker": "5", "prefix": "123",
                 "scale": {"start": 2, "length": 2},
                 "weight": {"start": 4, "length": 4}},
            ]
        },
    )
    code = "5" + "12345007"
    r = client.post("/api/decode", json={"code": code})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["matched_count"] == 2
    assert j["scale_no"] == "34"   # body[2:4]
    assert j["net_weight_g"] == 5007


def test_batch_clique_uses_three_fresh_digits(client):
    client.post(
        "/api/import",
        json={"templates": [
            {"name": "old", "marker": "1", "prefix": "9",
             "scale": {"start": 2, "length": 2},
             "weight": {"start": 4, "length": 4}}
        ]},
    )
    batch = [
        {"name": f"c{i}", "prefix": "",
         "scale": {"start": 0, "length": length},
         "weight": {"start": 0, "length": 1}}
        for i, length in enumerate([1, 2, 3])
    ]
    r = client.post("/api/assign", json={"templates": batch})
    assert r.status_code == 200, r.text
    j = r.json()
    assert len(set(j["markers"])) == 3
    assert "1" not in j["markers"]
    assert j["new_digit_count"] == 3
