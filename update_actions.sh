#!/bin/bash
# Script to update GitHub Actions from v3 to v4

set -e

WORKFLOW_FILE=".github/workflows/build.yml"

echo "Updating GitHub Actions to v4..."

# Backup the original file
cp "$WORKFLOW_FILE" "${WORKFLOW_FILE}.backup"
echo "✓ Created backup: ${WORKFLOW_FILE}.backup"

# Update actions/checkout from v3 to v4
sed -i.tmp 's/actions\/checkout@v3/actions\/checkout@v4/g' "$WORKFLOW_FILE"
echo "✓ Updated actions/checkout to v4"

# Update actions/setup-python from v4 to v5
sed -i.tmp 's/actions\/setup-python@v4/actions\/setup-python@v5/g' "$WORKFLOW_FILE"
echo "✓ Updated actions/setup-python to v5"

# Update actions/upload-artifact from v3 to v4
sed -i.tmp 's/actions\/upload-artifact@v3/actions\/upload-artifact@v4/g' "$WORKFLOW_FILE"
echo "✓ Updated actions/upload-artifact to v4"

# Update actions/download-artifact from v3 to v4
sed -i.tmp 's/actions\/download-artifact@v3/actions\/download-artifact@v4/g' "$WORKFLOW_FILE"
echo "✓ Updated actions/download-artifact to v4"

# Clean up temporary files
rm -f "${WORKFLOW_FILE}.tmp"

echo ""
echo "✅ All GitHub Actions updated successfully!"
echo ""
echo "Summary of changes:"
echo "  - actions/checkout: v3 → v4"
echo "  - actions/setup-python: v4 → v5"
echo "  - actions/upload-artifact: v3 → v4"
echo "  - actions/download-artifact: v3 → v4"
echo ""
echo "To restore from backup if needed:"
echo "  cp ${WORKFLOW_FILE}.backup $WORKFLOW_FILE"
