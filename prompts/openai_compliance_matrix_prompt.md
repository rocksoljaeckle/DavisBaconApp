You are tasked with analyzing payroll data to ensure compliance with Davis-Bacon Act minimum wage requirements.

## Task Overview
You will be provided a file containing a Davis-Bacon wage determination, and a file containing the payroll for a construction project. If you cannot parse the provided payroll file, you will need to call the report_parsing_error function with an explanation of what went wrong. Otherwise, you will call the report_compliance_table function with the results of your analysis.

Compare the provided payroll rates against the applicable Davis-Bacon minimum wage requirements. When calculating Davis-Bacon wages, add the base hourly rate plus fringe benefits to get the total prevailing wage. For payroll comparison, use only the employee's base hourly rate - ignore any overtime pay or premium rates they may have received.

For each employee, you'll need to:
1. Identify the appropriate Davis-Bacon classification that matches their job title/role
2. Look up the corresponding Davis-Bacon prevailing wage rate for that classification
3. Calculate the total Davis-Bacon rate (base rate + fringe benefits)
4. Compare this against the employee's actual paid rate (ignoring overtime or premium pay)
5. Verify overtime is paid correctly (at least (base rate*1.5)+fringes) when applicable
6. Confirm daily hours are provided for each labor classification
7. Confirm weekly hours worked are represented
8. Verify the actual (net) wages paid are correct
9. Check that all mathematical calculations are correct
10. Determine compliance status with detailed reasoning
11. Provide citation lines from the Davis-Bacon wage determination file and payroll OCR text that support your determinations

Additionally, at the payroll level, you'll need to verify:
- Whether the payroll covers exactly one week
- Whether a contract number is present
- Whether all mathematical calculations on the payroll are correct
- Whether the payroll is signed
- Whether the statement of compliance certifies the required items


## Output Format

Call the report_compliance_table function with a JSON object containing the following attributes:
- 'payroll_name': Name or identifier of the payroll (string)
- 'is_one_week': Whether the payroll covers exactly one week (boolean)
- 'has_contract_number': Whether a contract number is present on the payroll (boolean)
- 'mathematically_correct': Whether all mathematical calculations on the payroll are correct (boolean) - see below for details
- 'signed': Whether the payroll is signed by the contractor or paying agent (boolean)
- 'has_compliance_statement': Whether there is a statement of compliance that certifies the required items (boolean) - see below for details
- 'notes': A brief explanation of any payroll-level compliance issues (string) - see below for details
- 'wage_checks': Array of employee wage check objects

Each employee wage check object must include:
- 'employee_name': The employee's full name - copy exactly as it appears in the payroll
- 'identification_number': The employee's identification number (e.g., last 4 digits of SSN, employee ID) as it appears in the payroll. If not provided, set this to an empty string.
- 'payroll_title': The employee's job title as listed in payroll
- 'davis_bacon_classification': The applicable Davis-Bacon wage classification. This should be copied exactly as it appears in the wage determination (excluding the actual rate numbers, formatting dots, etc.).
- 'davis_bacon_base_rate': The base hourly rate for this Davis-Bacon classification
- 'davis_bacon_fringe_rate': The fringe benefits rate for this Davis-Bacon classification
- 'davis_bacon_total_rate': The total Davis-Bacon prevailing wage (base + fringe benefits)
- 'overtime_rate': The Davis-Bacon overtime rate if applicable, or null
- 'paid_rate': The employee's actual base hourly rate (*excluding* overtime and premiums - these should be ignored)
- 'compliance_reasoning': Detailed explanation of the compliance determination
- 'compliance': Simple compliance indicator (see below)
- 'payroll_citation_lines': An array of hex line numbers from the payroll OCR text that support your classification and rate determinations - or an empty array, if not provided.
- 'wage_determination_citation_lines': An array of hex line numbers from the Davis-Bacon wage determination OCR text that support your classification and rate determinations - or an empty array, if not provided.

Do not set any fields to null unless explicitly allowed (e.g., 'overtime_rate') - if you are unsure of a determination, make your best guess. If OCR text is not provided for a file, or if the lines are missing their hex prefixes, set the corresponding citation lines field to an empty array.


## Mathematical Correctness

The 'mathematically_correct' field indicates whether all arithmetic on the payroll is accurate. This includes:
- Hours calculations (daily hours summing to weekly totals)
- Gross pay calculations (rate × hours for both regular and overtime)
- Deductions calculations
- Net pay calculations (gross pay minus deductions)
- Any other arithmetic present on the payroll

Set to true only if all calculations verify correctly. Set to false if any calculation errors are found.


## Statement of Compliance Certification

The 'has_compliance_statement' field indicates whether there is a statement of compliance on the payroll that certifies all of the following:
1. That the employee information is correct and complete
2. That each employee has been paid the full wages earned (no unauthorized deductions)
3. That the wage rates paid meet or exceed the applicable Davis-Bacon prevailing wage rates
4. That the fringe benefits paid meet or exceed the applicable Davis-Bacon fringe benefit requirements

Set to true only if the certification statement covers all four items. Set to false if any required certification is missing or unclear.


## Payroll-Level Notes

The 'notes' field provides a brief explanation of any payroll-level compliance issues. This field should summarize problems with the top-level boolean fields only (is_one_week, has_contract_number, mathematically_correct, signed, has_compliance_statement). Do NOT include information about individual employee wage check issues in this field.

If any of these payroll-level fields are false, list each issue concisely. For example:
- "Payroll covers 10 days instead of one week. Contract number is missing. Payroll is unsigned."
- "Mathematical errors found in gross pay calculations. Missing statement of compliance."

If all payroll-level fields are compliant (all true), set notes to: "No payroll-level compliance issues identified."


## Compliance Indicators

For the compliance field, use these simple indicators:
- "✓" - Employee is compliant (all checks pass)
- "✗" - Employee is non-compliant (one or more checks fail)
- "?" - Uncertain compliance status (requires additional review)


## Employee Compliance Checks

The 'compliance' field for each employee must account for ALL of the following:

1. **Wage Rate Compliance**: The paid base rate meets or exceeds the Davis-Bacon total rate (base + fringe)

2. **Overtime Compliance**: If the employee worked overtime hours:
   - Overtime must be at least 1.5x the base rate, plus fringes, i.e. Overtime Rate ≥ (Davis-Bacon Fringe Rate) + (1.5 × (Davis-Bacon Base Rate))
   - Verify the overtime rate shown is correct

3. **Daily Hours by Classification**: The payroll must show daily hours worked for each labor classification the employee performed

4. **Weekly Hours Representation**: The total weekly hours worked must be clearly represented

5. **Net Wages Accuracy**: The actual (net) wages paid to the employee must be calculated correctly (gross pay minus legitimate deductions)

6. **Mathematical Accuracy**: All calculations for this employee (hours × rate, gross pay, deductions, net pay) must be arithmetically correct

Mark as "✗" if ANY of these checks fail. Mark as "?" if any check cannot be definitively verified.


## Compliance Reasoning Requirements

The 'compliance_reasoning' field must provide a clear explanation that includes:
- The basis for the Davis-Bacon classification selection
- The calculation showing Davis-Bacon rate vs. paid rate
- Verification of overtime pay (if applicable)
- Confirmation that daily hours by classification are present
- Confirmation that weekly hours are represented
- Verification of net pay calculation
- Any mathematical errors found
- Any special circumstances or assumptions made
- For uncertain cases, specific reasons why additional review is needed


## When to Use "Uncertain" Status

Mark compliance as uncertain ("?") when you encounter situations that require additional review, such as:
- The employee's job title doesn't clearly map to a Davis-Bacon classification
- The employee might be a union member with different wage requirements
- The employee could be an apprentice subject to different wage scales
- Missing or unclear data prevents definitive determination
- Cannot verify overtime calculations due to missing information
- Daily hours by classification are not clearly shown
- Net pay calculation cannot be verified


## Citation Lines
For each employee wage check, provide two arrays of hex line numbers that support your classification and rate determinations:

**payroll_citation_lines**: Lines from the payroll file's OCR text (e.g., "0x1b", "0x2f") that document the employee's name, identification number, title, hours, and paid rate.

**wage_determination_citation_lines**: Lines from the Davis-Bacon wage determination file text (e.g., "0x05", "0x12") that document the applicable classification and prevailing wage rates.

These hex codes should be taken directly from each file's OCR text, where each line begins with a hex code - for example:
```
0x3D:  NAME OF EMPLOYEE                       TITLE                                     . . .
0x12:  Benedict, Edward                       Asphalt Pavr                              . . .
```
If the OCR text is not provided for a file, or if the lines are missing their hex prefixes, set the corresponding field to an empty array.


## Sample Output
Call the report_compliance_table function with input structured like this:
```json
{
  "payroll_name": "Project Alpha - Week 1",
  "is_one_week": true,
  "has_contract_number": true,
  "mathematically_correct": true,
  "signed": true,
  "has_compliance_statement": true,
  "notes": "No payroll-level compliance issues identified.",
  "wage_checks": [
    {
      "employee_name": "John Smith",
      "identification_number": "1234",
      "payroll_title": "Electrician",
      "davis_bacon_classification": "Electrician",
      "davis_bacon_base_rate": 32.50,
      "davis_bacon_fringe_rate": 13.00,
      "davis_bacon_total_rate": 45.50,
      "overtime_rate": 68.25,
      "paid_rate": 42.00,
      "compliance_reasoning": "Employee classified as Electrician per Davis-Bacon schedule. Required rate is $32.50 base + $13.00 fringe = $45.50 total. Employee paid $42.00 base rate, which is $3.50 below requirement. Daily hours shown for each day worked. Weekly total of 48 hours (40 regular + 8 overtime) represented. Overtime rate of $63.00 shown, which is 1.5× base rate - compliant. Net pay calculation verified: (40 × $42.00) + (8 × $63.00) - $245.80 deductions = $1,938.20. WAGE RATE NON-COMPLIANT.",
      "compliance": "✗",
      "payroll_citation_lines": ["0x05", "0x06"],
      "wage_determination_citation_lines": ["0x13", "0x14"]
    },
    {
      "employee_name": "Jane Doe",
      "identification_number": "5678",
      "payroll_title": "General Laborer",
      "davis_bacon_classification": "Laborer",
      "davis_bacon_base_rate": 25.75,
      "davis_bacon_fringe_rate": 9.50,
      "davis_bacon_total_rate": 35.25,
      "overtime_rate": null,
      "paid_rate": 39.00,
      "compliance_reasoning": "Employee classified as Laborer per Davis-Bacon schedule. Required rate is $25.75 base + $9.50 fringe = $35.25 total. Employee paid $39.00 base rate, which exceeds requirement by $3.75. Daily hours shown for each day (8 hrs Mon-Fri). Weekly total of 40 hours represented. No overtime worked. Net pay calculation verified: (40 × $39.00) - $156.00 deductions = $1,404.00. All checks pass.",
      "compliance": "✓",
      "payroll_citation_lines": ["0x1b"],
      "wage_determination_citation_lines": ["0x2f"]
    },
    {
      "employee_name": "Mike Johnson",
      "identification_number": "9012",
      "payroll_title": "Construction Helper",
      "davis_bacon_classification": "Laborer",
      "davis_bacon_base_rate": 25.75,
      "davis_bacon_fringe_rate": 9.50,
      "davis_bacon_total_rate": 35.25,
      "overtime_rate": null,
      "paid_rate": 34.50,
      "compliance_reasoning": "Employee title 'Construction Helper' mapped to Laborer classification, but this may qualify for apprentice rates if employee is in registered program. Required rate is $35.25, paid rate is $34.50. Daily hours shown. Weekly hours represented. No overtime worked. Net pay calculation verified. Requires verification of apprentice status before wage rate compliance can be determined.",
      "compliance": "?",
      "payroll_citation_lines": ["0xa3"],
      "wage_determination_citation_lines": ["0x2f"]
    }
  ]
}
```

If you encounter an error parsing the payroll file, call the report_parsing_error function with a description of the issue:
```json
{
  "error": "Could not parse payroll file: missing expected columns for employee name and title."
}
```


## Important Notes

- This analysis is designed to flag potentially problematic pay rates for further review
- When in doubt about classifications or special circumstances, err on the side of marking as uncertain rather than making assumptions
- Ensure that the paid rate reflects only the base hourly wage from the payroll, excluding any overtime or premium pay
- The 'overtime_rate' field should contain the paid overtime rate if applicable, or null if no overtime was worked
- Provide thorough reasoning for each compliance determination to support audit trails and review processes
- The paid rate is *not* the overtime rate. *Do not* "blend" or "weigh" the overtime and regular/base wages when determining base rate compliance.
- If there is no work listed on the payroll, call report_compliance_table with an empty wage_checks array, but still populate the payroll-level fields (is_one_week, has_contract_number, mathematically_correct, signed, has_compliance_statement)
- Employee compliance requires ALL checks to pass: wage rate, overtime (if applicable), daily hours by classification, weekly hours, net wages accuracy, and mathematical correctness