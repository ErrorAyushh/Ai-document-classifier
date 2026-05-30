# test_classification.py
# Tests classification on every image in a folder
# Run: python test_classification.py --folder test_images
# Run on specific PDF folder: python test_classification.py --folder test_pdfs\pdf1_images

import os
import json
import argparse
from collections import Counter
from page_classifier_ayush import classify_all_pages

parser = argparse.ArgumentParser(description="Test classification on all images in a folder")
parser.add_argument("--folder", required=True, help="Folder containing page images")
args = parser.parse_args()

# Find all PNG/JPEG images in the folder
VALID_EXTENSIONS = {".png", ".jpg", ".jpeg"}

page_images = []
for filename in sorted(os.listdir(args.folder)):
    ext = os.path.splitext(filename)[1].lower()
    if ext in VALID_EXTENSIONS:
        page_images.append({
            "page_number": len(page_images) + 1,
            "image_path":  os.path.join(args.folder, filename)
        })

if not page_images:
    print(f"No images found in {args.folder}")
    exit()

print(f"\n{'='*60}")
print(f"CLASSIFICATION TEST — {args.folder}")
print(f"{'='*60}")
print(f"Found {len(page_images)} images to classify\n")

# Classify all pages
results = classify_all_pages(page_images)

# Print results
print("RESULTS:")
print("-"*60)
for r in results:
    icon = "✓" if r["confidence"] == "HIGH" else "⚠"
    filename = os.path.basename(r["image_path"])
    print(f"  {icon} {filename:<25} → {r['page_type']:<15} ({r['confidence']})")

# Summary
print("-"*60)
type_counts = Counter(r["page_type"] for r in results)
print("\nSUMMARY:")
for page_type, count in type_counts.items():
    print(f"  {page_type:<15} → {count} page(s)")

low_conf = [r for r in results if r["confidence"] == "LOW"]
if low_conf:
    print(f"\n  ⚠ {len(low_conf)} page(s) flagged — review manually:")
    for r in low_conf:
        print(f"    Page {r['page_number']}: {os.path.basename(r['image_path'])}")
else:
    print(f"\n  ✓ All pages HIGH confidence")

# Save results
output_file = f"classification_results_{os.path.basename(args.folder)}.json"
with open(output_file, "w") as f:
    json.dump(results, f, indent=2)
print(f"\nResults saved to: {output_file}")
print(f"{'='*60}\n")