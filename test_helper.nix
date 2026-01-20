{
  buildPythonPackage,
  setuptools,
  nixos-test-driver,
  ruff,
  pyright,
}:

buildPythonPackage {
  pname = "test_helper";
  version = "0.1";

  src = ./test_helper;

  format = "pyproject";

  nativeBuildInputs = [ setuptools ];
  buildInputs = [ nixos-test-driver ];
  nativeCheckInputs = [
    ruff
    pyright
  ];

  checkPhase = ''
    pyright test_helper
    ruff check .
    ruff format --check --diff .
  '';
}
