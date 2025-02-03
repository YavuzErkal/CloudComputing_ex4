#!/bin/bash

# Usage: ./build_and_push.sh <TAG_NUMBER>
# Example: ./build_and_push.sh 9

# Check if a tag number is provided
if [ -z "$1" ]; then
    echo "Usage: $0 <TAG_NUMBER>"
    exit 1
fi

TAG_NUMBER=$1

# Build the Docker image
docker build -t scheduler .

# Tag the image with the provided tag number
docker tag scheduler yavuzerkal/scheduler:$TAG_NUMBER

# Push the tagged image to Docker Hub
docker push yavuzerkal/scheduler:$TAG_NUMBER

echo "Docker image yavuzerkal/scheduler:$TAG_NUMBER built and pushed successfully!"
