---
model: sonnet
max_turns: 8
timeout_seconds: 240
allowed_tools: [Skill]
---
`CREATE DATABASE reporting FROM staging AS PERM = 2e9;` just failed with:

    [Error 3541] The request to assign new PERMANENT space is invalid.

The system has plenty of free space overall. What is actually wrong and how do I fix it?
