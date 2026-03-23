{
  description = "UltiMaker Cura build environment";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs { inherit system; };

        python = pkgs.python312;

        pythonWithPackages = python.withPackages (ps: with ps; [
          pip
          virtualenv
        ]);
      in
      {
        devShells.default = pkgs.mkShell {
          name = "cura-dev";

          buildInputs = with pkgs; [
            # Python
            pythonWithPackages

            # Build tools
            cmake
            ninja
            pkg-config
            gettext

            # C/C++ toolchain (Conan may build some deps from source)
            gcc
            gnumake

            # Version control (needed by conanfile.py for git operations)
            git

            # Needed by some Conan recipes
            autoconf
            automake
            libtool

            # SSL (needed by pip/conan for HTTPS)
            openssl
            cacert
          ];

          shellHook = ''
            export CURA_ROOT="$(pwd)"

            # Set up Python virtual environment
            if [ ! -d .venv ]; then
              echo "Creating Python virtual environment..."
              ${pythonWithPackages}/bin/python -m venv .venv
              source .venv/bin/activate
              echo "Installing Conan and build dependencies..."
              pip install --upgrade pip
              pip install "conan>=2.7.0" jinja2 pyyaml requests gitpython pyinstaller
            else
              source .venv/bin/activate
            fi

            # Ensure Conan is available
            if ! command -v conan &> /dev/null; then
              echo "WARNING: Conan not found in venv. Run: pip install 'conan>=2.7.0'"
            else
              echo "Cura dev shell ready. Conan $(conan --version)"
            fi

            echo ""
            echo "Next steps:"
            echo "  1. conan config install https://github.com/ultimaker/conan-config.git"
            echo "  2. conan install . --build=missing --update"
            echo "  3. conan build ."
            echo ""
          '';

          # Ensure Conan can find system SSL certs
          NIX_SSL_CERT_FILE = "${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt";
          SSL_CERT_FILE = "${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt";
        };
      }
    );
}