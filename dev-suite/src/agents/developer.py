"""Lead Dev agent - executes Blueprints, writes and refactors code.

Uses Claude 4.5 (Anthropic) or DeepSeek-Coder.
Runs code in E2B sandbox, never on host.

Implementation in Step 4.
"""

# TODO Step 4:
# - Define developer prompt template (receives Blueprint JSON)
# - Configure model selection (anthropic vs deepseek based on .env)
# - Implement code generation with sandbox execution
# - Handle retry with QA failure report context
