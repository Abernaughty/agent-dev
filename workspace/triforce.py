"""Triforce ASCII Art Generator

Programmatically generates and prints an ASCII art Triforce using loops
and string operations. No hardcoded multi-line string literals are used.
"""


def generate_triangle(h):
    """Generate a single triangle as a list of strings.

    Each row i (0-indexed) of a triangle of height h contains (2*i + 1)
    asterisks, centered within a field of width (2*h - 1).

    Args:
        h: Height of the triangle in rows.

    Returns:
        A list of strings, one per row of the triangle.
    """
    width = 2 * h - 1
    rows = []
    for i in range(h):
        stars = '*' * (2 * i + 1)
        row = stars.center(width)
        rows.append(row)
    return rows


def print_triforce(h):
    """Build and print a Triforce pattern of three triangles.

    The Triforce consists of one triangle centered on top and two triangles
    side by side on the bottom. All three triangles have the same height h.

    The full output width is calculated as:
        full_width = 2 * (2*h - 1) + 1
    This accommodates two side-by-side triangles with a single space gap.

    Args:
        h: Height of each individual triangle in rows.
    """
    triangle = generate_triangle(h)
    tri_width = 2 * h - 1
    full_width = 2 * tri_width + 1

    # Print the top triangle, centered in the full width
    for row in triangle:
        print(row.center(full_width))

    # Print the bottom two triangles side by side
    for row in triangle:
        # Left triangle line + space + right triangle line
        combined = row + ' ' + row
        print(combined.center(full_width))


if __name__ == '__main__':
    print_triforce(5)
