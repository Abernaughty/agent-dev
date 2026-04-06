#!/usr/bin/env python3
"""
Triforce ASCII Art Generator

This script prints the iconic Triforce symbol from The Legend of Zelda series
using ASCII characters. The Triforce consists of three triangles arranged
with one triangle on top and two triangles side-by-side on the bottom.
"""

def print_triforce():
    """
    Prints the Triforce symbol using ASCII characters.
    
    The Triforce is composed of:
    - One triangle centered at the top
    - Two triangles side-by-side at the bottom
    """
    triforce = """
      /\\
     /  \\
    /____\\
   /\\    /\\
  /  \\  /  \\
 /____\\/____\\
"""
    print(triforce)

def main():
    """
    Main function to execute the Triforce display.
    """
    print("The Legend of Zelda - Triforce")
    print("=" * 30)
    print_triforce()
    print("=" * 30)

if __name__ == "__main__":
    main()
