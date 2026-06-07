# CSV vs Hand-Authored Trait Catalogues - Gap Analysis

Generated 2026-05-10.

## lumi.test.bulb - Test Bulb

### Traits in CSV but not in hand-authored package
- `1.6.85` (`min_brightness`) - uint, Readable+Writable
  - Description: Minimum brightness percentage

### Traits in hand-authored package but not in CSV
- `9.9.99` (`hand_only`) - number

### Type or shape mismatches
- (none)

## lumi.test.gate - Test Gate

### Traits in CSV but not in hand-authored package
- (none)

### Traits in hand-authored package but not in CSV
- (none)

### Type or shape mismatches
- `4.1.85` (`state`): CSV data_type=bool enum_values=('0', '1'), authored data_type=uint enum_values=('open', 'closed')
