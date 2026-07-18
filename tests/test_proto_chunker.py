"""Tests unitaires pour proto_chunker.py (ProtoChunker)."""
from __future__ import annotations

from pathlib import Path

from proto_chunker import ProtoChunker


def _write(tmp_path: Path, name: str, source: str) -> Path:
    p = tmp_path / name
    p.write_text(source, encoding="utf-8")
    return p


SIMPLE_PROTO = """\
syntax = "proto3";
package myapp.v1;

import "google/protobuf/timestamp.proto";

message UserRequest {
  string user_id = 1;
  int32 page_size = 2;
}

message UserResponse {
  string name = 1;
  int32 age = 2;
}

service UserService {
  rpc GetUser (UserRequest) returns (UserResponse);
  rpc ListUsers (UserRequest) returns (UserResponse);
}

enum Status {
  STATUS_UNSPECIFIED = 0;
  STATUS_ACTIVE = 1;
  STATUS_INACTIVE = 2;
}
"""


class TestProtoChunkerBasics:
    def test_empty_file_returns_empty(self, tmp_path):
        f = _write(tmp_path, "empty.proto", "")
        assert ProtoChunker().chunk(f, tmp_path) == []

    def test_returns_code_chunks(self, tmp_path):
        from code_chunker import CodeChunk
        f = _write(tmp_path, "api.proto", SIMPLE_PROTO)
        chunks = ProtoChunker().chunk(f, tmp_path)
        assert all(isinstance(c, CodeChunk) for c in chunks)

    def test_language_is_proto(self, tmp_path):
        f = _write(tmp_path, "api.proto", SIMPLE_PROTO)
        chunks = ProtoChunker().chunk(f, tmp_path)
        assert all(c.language == "proto" for c in chunks)

    def test_relative_path(self, tmp_path):
        f = _write(tmp_path, "api.proto", SIMPLE_PROTO)
        chunks = ProtoChunker().chunk(f, tmp_path)
        assert all(c.relative_path == "api.proto" for c in chunks)


class TestProtoChunkerTypes:
    def test_detects_messages(self, tmp_path):
        f = _write(tmp_path, "api.proto", SIMPLE_PROTO)
        chunks = ProtoChunker().chunk(f, tmp_path)
        types = {c.chunk_type for c in chunks}
        assert "message" in types

    def test_detects_service(self, tmp_path):
        f = _write(tmp_path, "api.proto", SIMPLE_PROTO)
        chunks = ProtoChunker().chunk(f, tmp_path)
        types = {c.chunk_type for c in chunks}
        assert "service" in types

    def test_detects_enum(self, tmp_path):
        f = _write(tmp_path, "api.proto", SIMPLE_PROTO)
        chunks = ProtoChunker().chunk(f, tmp_path)
        types = {c.chunk_type for c in chunks}
        assert "enum" in types

    def test_symbol_names_correct(self, tmp_path):
        f = _write(tmp_path, "api.proto", SIMPLE_PROTO)
        chunks = ProtoChunker().chunk(f, tmp_path)
        names = {c.symbol_name for c in chunks}
        assert "UserRequest" in names
        assert "UserResponse" in names
        assert "UserService" in names
        assert "Status" in names


class TestProtoChunkerHeader:
    def test_package_in_chunk_content(self, tmp_path):
        f = _write(tmp_path, "api.proto", SIMPLE_PROTO)
        chunks = ProtoChunker().chunk(f, tmp_path)
        for c in chunks:
            assert "package myapp.v1" in c.content

    def test_file_path_in_content(self, tmp_path):
        f = _write(tmp_path, "api.proto", SIMPLE_PROTO)
        chunks = ProtoChunker().chunk(f, tmp_path)
        for c in chunks:
            assert "api.proto" in c.content


class TestProtoChunkerRefs:
    def test_service_refs_contain_request_response_types(self, tmp_path):
        f = _write(tmp_path, "api.proto", SIMPLE_PROTO)
        chunks = ProtoChunker().chunk(f, tmp_path)
        svc = next(c for c in chunks if c.chunk_type == "service")
        assert "UserRequest" in svc.symbols_referenced
        assert "UserResponse" in svc.symbols_referenced

    def test_message_refs_contain_field_types(self, tmp_path):
        source = """\
syntax = "proto3";

message Outer {
  InnerType value = 1;
  repeated OtherType items = 2;
}

message InnerType {
  string x = 1;
}

message OtherType {
  int32 y = 1;
}
"""
        f = _write(tmp_path, "refs.proto", source)
        chunks = ProtoChunker().chunk(f, tmp_path)
        outer = next(c for c in chunks if c.symbol_name == "Outer")
        assert "InnerType" in outer.symbols_referenced
        assert "OtherType" in outer.symbols_referenced


class TestProtoChunkerLineCounting:
    def test_start_end_lines_set(self, tmp_path):
        f = _write(tmp_path, "api.proto", SIMPLE_PROTO)
        chunks = ProtoChunker().chunk(f, tmp_path)
        for c in chunks:
            assert c.start_line >= 1
            assert c.end_line > c.start_line

    def test_file_hash_set(self, tmp_path):
        f = _write(tmp_path, "api.proto", SIMPLE_PROTO)
        chunks = ProtoChunker().chunk(f, tmp_path)
        assert all(len(c.file_hash) == 64 for c in chunks)


class TestProtoExtractHeader:
    def test_extracts_syntax_package_imports(self):
        chunker = ProtoChunker()
        result = chunker._extract_header(SIMPLE_PROTO)
        assert "syntax" in result
        assert "package" in result
        assert "import" in result

    def test_empty_source_returns_empty(self):
        chunker = ProtoChunker()
        assert chunker._extract_header("") == ""
