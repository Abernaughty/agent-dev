#!/usr/bin/env python3
"""
Triforce ASCII Art Script

This script prints a Triforce pattern to the console.
The Triforce consists of three triangles arranged in a triangular formation.
"""

def print_triforce():
    """
    Prints the Triforce ASCII art pattern.
    
    The pattern consists of:
    - One triangle at the top (centered)
    - Two triangles at the bottom (side by side)
    """
    triforce = """      /\\
     /  \\
    /____\\
   /\\    /\\
  /  \\  /  \\
 /____\\/____\\"""
    
    print(triforce)

if __name__ == "__main__":
    print_triforce()