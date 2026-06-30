---
name: code-review
description: Use this skill when reviewing code changes to ensure quality, catch bugs, and maintain coding standards. Follows a systematic approach to code review.
---

# Code Review Skill

## Overview
This skill provides a structured approach to reviewing code changes. It helps the agent systematically examine pull requests for potential issues, bugs, and areas for improvement.

## Instructions
### 1. Understand the Changes
- First, examine the pull request description to understand what changes are being made and why
- Review the list of changed files to get an overview of the scope

### 2. Examine Each Changed File
For each file that has been modified:
- Look at the specific changes made (using git diff or similar)
- Consider whether the changes address the stated purpose
- Check for obvious bugs, logical errors, or confusing code

### 3. Check for Common Issues
Review the code for these common problems:
- Logic errors: Incorrect conditionals, off-by-one errors, infinite loops
- Resource leaks: Unclosed file handles, database connections, or network resources
- Error handling: Missing or inadequate error handling
- Security issues: Potential injection vulnerabilities, improper validation
- Performance issues: Inefficient algorithms, unnecessary computations
- Code style: Deviations from project coding standards
- Test coverage: Lack of tests for new functionality

### 4. Verify Best Practices
Ensure the code follows these best practices:
- Functions have a single responsibility
- Variable and function names are descriptive
- Comments explain why, not what
- Complex logic is broken down into smaller functions
- Edge cases are considered and handled

### 5. Provide Feedback
When providing feedback:
- Be specific about what you found and where
- Explain why something is a problem or could be improved
- Suggest concrete changes when possible
- Balance criticism with recognition of what's done well
- Prioritize feedback by importance and impact

### 6. Document Your Review
- Keep track of issues found and their severity
- Note any questions you have about the changes
- Summarize your overall assessment of the code quality
