You are tasked with analyzing payroll data to ensure compliance with Davis-Bacon Act minimum wage requirements for a single employee.

## Task Overview
You will be provided a file containing a Davis-Bacon wage determination, a file containing the payroll for a construction project, and an employee's name. If you cannot parse the provided payroll file, you will need to respond with JSON indicating the failure. Otherwise, you will respond with JSON containing the results of your analysis. See below for details on the exact output format.

Compare the payroll rate for the given employee against the applicable Davis-Bacon minimum wage requirements. When calculating the employee's Davis-Bacon wage, add the base hourly rate plus fringe benefits to get the total prevailing wage. For payroll comparison, use only the employee's base hourly rate - ignore any overtime pay or premium rates they may have received.

For the given employee, you'll need to:
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
11. Provide citation lines from the Davis-Bacon wage determination file and payroll OCR text that support your determination - or an empty array if not available.


## Output Format

If you are unable to parse the provided payroll file, respond with JSON containing 'success': false and a 'notes' field explaining the issue. For example:
```json
{
  "success": false,
  "notes": "Could not parse payroll file: missing expected columns for employee name and title."
}
```

If you *are* able to parse the payroll file, provide your analysis as a JSON object with a 'success' (true) attribute, and the attributes listed below:
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


## Compliance Indicators

For the compliance field, use these simple indicators:
- "✓" - Employee is compliant (all checks pass)
- "✗" - Employee is non-compliant (one or more checks fail)
- "?" - Uncertain compliance status (requires additional review)


## Employee Compliance Checks

The 'compliance' field must account for ALL of the following:

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
Provide two arrays of hex line numbers that support your classification and rate determination:

**payroll_citation_lines**: Lines from the payroll file's OCR text (e.g., "0x1b", "0x2f") that document the employee's name, identification number, title, hours, and paid rate.

**wage_determination_citation_lines**: Lines from the Davis-Bacon wage determination file text (e.g., "0x05", "0x12") that document the applicable classification and prevailing wage rates.

These hex codes should be taken directly from each file's OCR text, where each line begins with a hex code - for example:
```
0x3D:  NAME OF EMPLOYEE                       TITLE                                     . . .
...
0x12:  Benedict, Edward                       Asphalt Pavr                              . . .
```
If the OCR text is not provided for a file, or if the lines are missing their hex prefixes, set the corresponding field to an empty array.


## Success Output Structure
If you are able to successfully parse the payroll file and perform the analysis, respond with JSON structured according to the following schema:
```json
{
  "properties": {
    "success": {
      "title": "Success",
      "type": "boolean"
    },
    "employee_name": {
      "title": "Employee Name",
      "type": "string"
    },
    "identification_number": {
      "title": "Identification Number",
      "type": "string"
    },
    "payroll_title": {
      "title": "Payroll Title",
      "type": "string"
    },
    "davis_bacon_classification": {
      "title": "Davis Bacon Classification",
      "type": "string"
    },
    "davis_bacon_base_rate": {
      "title": "Davis Bacon Base Rate",
      "type": "number"
    },
    "davis_bacon_fringe_rate": {
      "title": "Davis Bacon Fringe Rate",
      "type": "number"
    },
    "davis_bacon_total_rate": {
      "title": "Davis Bacon Total Rate",
      "type": "number"
    },
    "overtime_rate": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Overtime Rate"
    },
    "paid_rate": {
      "title": "Paid Rate",
      "type": "number"
    },
    "compliance_reasoning": {
      "title": "Compliance Reasoning",
      "type": "string"
    },
    "compliance": {
      "title": "Compliance",
      "type": "string"
    },
    "payroll_citation_lines": {
      "items": {
        "type": "string"
      },
      "title": "Payroll Citation Lines",
      "type": "array"
    },
    "wage_determination_citation_lines": {
      "items": {
        "type": "string"
      },
      "title": "Wage Determination Citation Lines",
      "type": "array"
    }
  },
  "required": [
    "success",
    "employee_name",
    "identification_number",
    "payroll_title",
    "davis_bacon_classification",
    "davis_bacon_base_rate",
    "davis_bacon_fringe_rate",
    "davis_bacon_total_rate",
    "overtime_rate",
    "paid_rate",
    "compliance_reasoning",
    "compliance",
    "payroll_citation_lines",
    "wage_determination_citation_lines"
  ],
  "title": "SingleEmployeeWageCheck",
  "type": "object"
}
```

Below is a sample output conforming to this schema:
```json
{
  "success": true,
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
}
```

If you encounter an error parsing the payroll file, respond with JSON structured like this:
```json
{
  "success": false,
  "notes": "Could not parse payroll file: missing expected columns for employee name and title."
}
```

Respond with JSON only, no additional text.


## Important Notes

- This analysis is designed to flag potentially problematic pay rates for further review
- When in doubt about classifications or special circumstances, err on the side of marking as uncertain rather than making assumptions
- Ensure that the paid rate reflects only the base hourly wage from the payroll, excluding any overtime or premium pay
- The 'overtime_rate' field should contain the paid overtime rate if applicable, or null if no overtime was worked
- Provide thorough reasoning for each compliance determination to support audit trails and review processes
- The paid rate is *not* the overtime rate. *Do not* "blend" or "weigh" the overtime and regular/base wages when determining base rate compliance.
- Employee compliance requires ALL checks to pass: wage rate, overtime (if applicable), daily hours by classification, weekly hours, net wages accuracy, and mathematical correctness