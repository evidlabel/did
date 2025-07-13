# Configuration

Generated `config.yaml` example:
```yaml
names:
  - id: <PERSON_1>
    variants: ["John Doe", "Jon Doe", "john DOE"]
  - id: <PERSON_2>
    variants: ["Jane Smith", "Jane Smyth"]
emails:
  - id: <EMAIL_ADDRESS_1>
    variants: ["john.doe@example.com"]
addresses:
  - id: <ADDRESS_1>
    variants: ["123 One Street, Springfield, US"]
numbers:
  - id: <PHONE_NUMBER_1>
    variants: ["1234567890", "12 34 56 78"]
    pattern: "\\b\\d{2}\\s+\\d{2}\\s+\\d{2}\\s+\\d{2}\\b"
cpr:
  - id: <CPR_NUMBER_1>
    variants: ["123456-1234"]
```

- **names**: Grouped person names (entity: PERSON).
- **emails**: Email addresses (entity: EMAIL_ADDRESS).
- **addresses**: Addresses (entity: ADDRESS).
- **numbers**: Phone numbers with optional patterns (entity: PHONE_NUMBER).
- **cpr**: CPR numbers (entity: CPR_NUMBER).
