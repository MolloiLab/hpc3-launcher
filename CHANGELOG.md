# Changelog

## [0.3.0](https://github.com/MolloiLab/hpc3-launcher/compare/v0.2.1...v0.3.0) (2026-06-17)


### Features

* show GPU VRAM in the GPU Type picker ([0543914](https://github.com/MolloiLab/hpc3-launcher/commit/0543914a7cd994f01cf505c9dd8601ea41614328))


### Bug Fixes

* clear stale known_hosts entry when writing a session's SSH config ([bb230f1](https://github.com/MolloiLab/hpc3-launcher/commit/bb230f10c255c4731b50c3ffc14077bd29b5228f))

## [0.2.1](https://github.com/MolloiLab/hpc3-launcher/compare/v0.2.0...v0.2.1) (2026-06-17)


### Bug Fixes

* document no-admin macOS install (drag to ~/Applications) ([8a7f48c](https://github.com/MolloiLab/hpc3-launcher/commit/8a7f48cbed5167f25411fbd952be8cb0e28cc5bb))

## [0.2.0](https://github.com/MolloiLab/hpc3-launcher/compare/v0.1.1...v0.2.0) (2026-06-17)


### Features

* account-driven cascade so only valid HPC3 configs are selectable ([5dd5201](https://github.com/MolloiLab/hpc3-launcher/commit/5dd520178a6ea3168a38fe65733f2f36aa96c254))


### Bug Fixes

* derive CPUs from memory using real per-partition limits ([406d045](https://github.com/MolloiLab/hpc3-launcher/commit/406d045c0244af0e870831c4db6a57441689a96c))
* keep every VSCode session's SSH config block (multi-session) ([cdd5afa](https://github.com/MolloiLab/hpc3-launcher/commit/cdd5afa33527c531c038cbc1acbd69315c45d289))

## [0.1.1](https://github.com/MolloiLab/hpc3-launcher/compare/v0.1.0...v0.1.1) (2026-06-16)


### Bug Fixes

* pin setuptools&lt;71 in CI builds so the frozen Linux app launches ([f52cecd](https://github.com/MolloiLab/hpc3-launcher/commit/f52cecd285616300202913bcf6b489ee169fa849))
