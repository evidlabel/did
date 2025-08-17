# Usage Workflow

This section provides a detailed workflow using examples from the `examples/` directory to demonstrate entity extraction and anonymization.

### Step 1: Entity Extraction
Use the `did extract` command to extract entities from input files and generate a configuration file. For example, to process `test_document.md` and save the configuration to `__temp.yaml`:
```bash
uv run did extract examples/test_document.md -c examples/__temp.yaml
```

This analyzes the input for names, emails, addresses, phone numbers, and CPR numbers, groups similar entities using fuzzy matching, and outputs a YAML file. Inspect `__temp.yaml`:
```bash
cat examples/__temp.yaml
```

You can manually edit this YAML file to customize replacement IDs or patterns before anonymization.

### Step 2: Anonymization
Use the `did pseudo` command to anonymize the input file based on the YAML config. For example:
```bash
uv run did pseudo examples/test_document.md -c examples/__temp.yaml -o examples/gaai.md
```

This replaces detected entities in `test_document.md` with their corresponding IDs (e.g., `<PERSON_1>`, `<PHONE_NUMBER_1>`). The anonymized output is saved to `gaai.md`. New entities not in the config will be assigned new IDs during anonymization.

### Step 3: Review Anonymized Output
Open `examples/gaai.md` to verify the anonymization. For instance, the line:
```
Contact John Doe at 1234567890 or Jane Smith via 12 34 56 78.
```
will be anonymized to:
```
Contact <PERSON_1> at <PHONE_NUMBER_1> or <PERSON_2> via <PHONE_NUMBER_1>.
```

This workflow ensures consistent replacement of sensitive information, handling variations through fuzzy matching and pattern recognition.

