"""Full file programmatic example of extraction and pseudonymization."""

from pathlib import Path
from did.core.anonymizer import Anonymizer
from did.file_utils import extract_text, anonymize_file, md_to_typst
import io
import ruamel.yaml as yaml
import re
import random

# Ensure temp directory exists
temp_dir = Path("examples/__temp")
temp_dir.mkdir(parents=True, exist_ok=True)

# Path to the test document
input_file = Path("examples/test_document.md")

# Create Anonymizer
anonymizer = Anonymizer(language="en")

# Extract text from the file
if input_file.exists():
    text = extract_text(input_file)
else:
    print(f"File {input_file} not found.")
    exit(1)

# Detect entities in the extracted text
anonymizer.detect_entities([text])

# Generate YAML config
yaml_config = anonymizer.generate_yaml()
print("Generated YAML config:")
print(yaml_config)

# Load replacements from the generated config
yaml_obj = yaml.YAML()
config_data = yaml_obj.load(io.StringIO(yaml_config))
anonymizer.load_replacements(config_data)

# Anonymize the text
anonymized_text, counts = anonymizer.anonymize(text)
print("\nAnonymized text:")
print(anonymized_text)
print("\nReplacement counts:")
for key, value in counts.items():
    if value > 0:
        print(f"  {key}: {value}")

# Example with file anonymization
output_file = temp_dir / "output.md"
counts = anonymize_file(input_file, anonymizer, output_file)
print(f"\nFile anonymized to {output_file}")
print("File replacement counts:")
for key, value in counts.items():
    if value > 0:
        print(f"  {key}: {value}")

# Typst export
main_path = temp_dir / "test_document.typ"
stem = main_path.stem
parent = main_path.parent
vars_path = parent / f"{stem}_vars.typ"
fake_path = parent / f"{stem}_fakevars.typ"

# Generate Typst mappings
var_counters = {
    "person": 0,
    "email_address": 0,
    "location": 0,
    "phone_number": 0,
    "date_number": 0,
    "id_number": 0,
    "code_number": 0,
    "general_number": 0,
}
category_mapping = {
    "person": "person_replaced",
    "email_address": "email_address_replaced",
    "location": "location_replaced",
    "phone_number": "phone_number_replaced",
    "date_number": "date_number_replaced",
    "id_number": "id_number_replaced",
    "code_number": "code_number_replaced",
    "general_number": "general_number_replaced",
}
typst_mappings = {}
fake_mappings = {}
all_replacements = []

# Helper functions
def generate_fake_digits(length):
    return "".join(
        str(random.randint(1 if i == 0 else 0, 9)) for i in range(length)
    )

def apply_format(variant, fake_digits):
    fake = ""
    d_idx = 0
    for char in variant:
        if char.isdigit():
            if d_idx < len(fake_digits):
                fake += fake_digits[d_idx]
                d_idx += 1
            else:
                fake += str(random.randint(0, 9))
        else:
            fake += char
    return fake

number_cats = [
    "phone_number",
    "date_number",
    "id_number",
    "code_number",
    "general_number",
]
for cat in var_counters:
    prefix = {
        "person": "P",
        "email_address": "E",
        "location": "A",
        "phone_number": "PH",
        "date_number": "DT",
        "id_number": "ID",
        "code_number": "CD",
        "general_number": "GN",
    }[cat]
    entities = getattr(anonymizer.entities, cat)
    for entity in entities:
        var_counters[cat] += 1
        ent_idx = var_counters[cat]
        sorted_variants = sorted(entity.variants, key=len, reverse=True)

        if cat in number_cats:
            if entity.variants:
                max_digit_len = max(
                    len(re.sub(r"\D", "", v)) for v in entity.variants
                )
                fake_digits = generate_fake_digits(max_digit_len)
            else:
                fake_digits = ""
        else:
            fake_digits = ""

        for v_idx, variant in enumerate(sorted_variants, 1):
            var = f"{prefix}{ent_idx}V{v_idx}"
            typst_mappings[var] = variant

            if cat == "person":
                fake_var = f"Person{ent_idx} Var{v_idx}"
            elif cat == "email_address":
                fake_var = f"email{ent_idx}var{v_idx}@example.com"
            elif cat == "location":
                fake_var = f"Address{ent_idx} Var{v_idx}"
            elif cat in number_cats:
                fake_var = apply_format(variant, fake_digits)
            else:
                fake_var = "<FAKE>"
            fake_mappings[var] = fake_var

            repl = f"#({var})"
            escaped = re.escape(variant)
            if (
                cat
                in [
                    "person",
                    "phone_number",
                    "date_number",
                    "id_number",
                    "code_number",
                    "general_number",
                ]
                and "\n" in variant
            ):
                pattern = escaped
            elif cat == "location":
                pattern = escaped
            else:
                pattern = r"\b" + escaped + r"\b"
            all_replacements.append(
                (
                    variant,
                    pattern,
                    repl,
                    cat,
                    entity.pattern if cat in number_cats else None,
                )
            )

# Sort replacements
sorted_replacements = sorted(
    all_replacements, key=lambda x: len(x[0]), reverse=True
)

# Apply replacements to text
for variant, pattern, repl, cat, pat in sorted_replacements:
    text = re.sub(pattern, repl, text)

anonymized_text = text

# Write vars.typ
with open(vars_path, "w", encoding="utf-8") as f:
    for var, val in typst_mappings.items():
        escaped = val.replace("\\", "\\\\").replace('"', '\\"')
        f.write(f'#let {var} = "{escaped}"\n')

# Write fakevars.typ
with open(fake_path, "w", encoding="utf-8") as f:
    for var, val in fake_mappings.items():
        escaped = val.replace("\\", "\\\\").replace('"', '\\"')
        f.write(f'#let {var} = "{escaped}"\n')

# Write main.typ
with open(main_path, "w", encoding="utf-8") as f:
    f.write(f'#import "{vars_path.name}": *\n\n')
    if input_file.suffix == ".md":
        f.write(md_to_typst(anonymized_text))
    else:
        f.write(anonymized_text)

print(f"\nTypst files written to {main_path.parent}")
print(f" - {main_path}")
print(f" - {vars_path}")
print(f" - {fake_path}")
