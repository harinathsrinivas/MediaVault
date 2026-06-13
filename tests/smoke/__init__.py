# Marks tests/smoke as a package so pytest's rootdir-relative imports and the
# parent tests/conftest.py fixtures (sandbox, sandbox_alias, mock_device,
# mock_fetch, fake_dummy, stub_tech_specs, FakeAdb, the _ffmpeg/_mkvmerge skip
# helpers) are inherited automatically — tests/smoke sits BELOW tests/, so its
# files see every fixture defined in tests/conftest.py without re-importing.
