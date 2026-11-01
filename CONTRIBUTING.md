# Contributing

Thanks for your interest in this project. Questions, bug reports, and pull
requests are all welcome.

## Never share real patient data

Do not put real patient information in issues, pull requests, commits, or
attachments. That includes names, dates of birth, record numbers, addresses,
phone numbers, and lab results, even partial or "anonymized by hand." Use the
synthetic data generators in this repository, or invent values.

If you think real data has been posted anywhere in this repository, delete it
right away and contact the maintainer.

## Running the tests

From the repository root, with the requirements installed:

```
cd surveillance_pipeline
python -m unittest discover tests -v
cd ../legacy_to_elr
python -m unittest discover tests -v
```

Every pull request should keep both suites passing. GitHub runs them
automatically when you push.

## Adding a code or a mapping

- Every LOINC and SNOMED CT code in `legacy_to_elr/mappings/` has the published
  source it was checked against recorded next to it. New codes need the same:
  include the source link in your pull request.
- A mapping from a lab's local test code to LOINC must be confirmed by the lab
  that performs the test. Similar-looking codes can mean very different things.

## Reporting a problem

Open an issue that says what you ran, what you expected, what happened, your
operating system and Python version, and the error output. Use synthetic data
only.

## Scope

This is a prototype for learning and demonstration. It is not intended for
clinical or production use.
