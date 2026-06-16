#!/bin/bash
# Script to set up GitHub repository and push code

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Print colored message
print_message() {
  echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
  echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
  echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
  echo -e "${RED}[ERROR]${NC} $1"
}

# Check if git is installed
if ! command -v git &> /dev/null; then
  print_error "Git is not installed. Please install git first."
  exit 1
fi

# Check if GitHub CLI is installed (optional)
HAS_GH=false
if command -v gh &> /dev/null; then
  HAS_GH=true
  print_message "GitHub CLI detected. Will use it for repository creation."
else
  print_warning "GitHub CLI not found. You'll need to create the repository manually."
fi

# Get repository name
read -p "Enter repository name [UCI-ClusterManager]: " REPO_NAME
REPO_NAME=${REPO_NAME:-UCI-ClusterManager}

# Get repository description
read -p "Enter repository description [A graphical tool for managing UCI HPC cluster resources]: " REPO_DESC
REPO_DESC=${REPO_DESC:-"A graphical tool for managing UCI HPC cluster resources"}

# Initialize git repository if not already initialized
if [ ! -d .git ]; then
  print_message "Initializing git repository..."
  git init
  print_success "Git repository initialized."
else
  print_message "Git repository already initialized."
fi

# Add files to git
print_message "Adding files to git..."
git add .
print_success "Files added."

# Commit changes
print_message "Committing changes..."
git commit -m "Initial commit: HPC3 Launcher v0.0.1"
print_success "Changes committed."

# Create GitHub repository if GitHub CLI is available
if [ "$HAS_GH" = true ]; then
  print_message "Creating GitHub repository..."
  gh repo create "$REPO_NAME" --description "$REPO_DESC" --public
  
  # Set remote origin
  git remote add origin "https://github.com/$(gh api user | jq -r .login)/$REPO_NAME.git"
else
  print_message "Please create a new repository on GitHub and then run:"
  echo "  git remote add origin https://github.com/YOUR_USERNAME/$REPO_NAME.git"
  
  # Ask for remote URL
  read -p "Enter the remote repository URL or press Enter to continue: " REMOTE_URL
  
  if [ ! -z "$REMOTE_URL" ]; then
    git remote add origin "$REMOTE_URL"
  else
    print_warning "No remote URL provided. You'll need to set it manually later."
    exit 0
  fi
fi

# Push to GitHub
print_message "Pushing code to GitHub..."
git push -u origin master || git push -u origin main

# Create v0.0.1 tag
print_message "Creating v0.0.1 tag..."
git tag -a v0.0.1 -m "Initial release version 0.0.1"
git push origin v0.0.1

print_success "Repository setup complete!"
print_message "Your code is now available on GitHub."
print_message "GitHub Actions will automatically build and create a release."

# Instructions for manual release if needed
print_message "If you need to manually create a release:"
echo "1. Go to your repository on GitHub"
echo "2. Click on 'Releases' on the right sidebar"
echo "3. Click 'Create a new release'"
echo "4. Enter 'v0.0.1' as the tag"
echo "5. Upload the built artifacts"

exit 0 