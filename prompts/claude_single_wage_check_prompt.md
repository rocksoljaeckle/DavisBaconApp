You are tasked with analyzing payroll data to ensure compliance with Davis-Bacon Act minimum wage requirements for a single employee.

## Task Overview
You will be provided a file containing Davis-Bacon wage rates, a file containing the payroll for a construction project, and an employee's name. If you cannot parse the provided payroll file, you will need to responsd with JSON indicating the failure. Otherwise, you will respond with JSON containing the results of your analysis. See below for details on the exact outputt format.

Compare the payroll rate for the given employee against the applicable Davis-Bacon minimum wage requirements. When calculating the employee's Davis-Bacon wage, add the base hourly rate plus fringe benefits to get the total prevailing wage. For payroll comparison, use only the employee's base hourly rate - ignore any overtime pay or premium rates they may have received.

For the given employee, you'll need to:
1. Identify the appropriate Davis-Bacon classification that matches their job title/role
2. Look up the corresponding Davis-Bacon prevailing wage rate for that classification
3. Calculate the total Davis-Bacon rate (base rate + fringe benefits)
4. Compare this against the employee's actual paid rate (ignoring overtime or premium pay)
5. Determine compliance status with detailed reasoning


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
- 'title': The employee's job title as listed in payroll
- 'davis_bacon_classification': The applicable Davis-Bacon wage classification
- 'davis_bacon_base_rate': The base hourly rate for this Davis-Bacon classification
- 'davis_bacon_fringe_rate': The fringe benefits rate for this Davis-Bacon classification
- 'davis_bacon_total_rate': The total Davis-Bacon prevailing wage (base + fringe benefits)
- 'overtime_rate': The Davis-Bacon overtime rate if applicable, or null
- 'paid_rate': The employee's actual base hourly rate (excluding overtime/premiums)
- 'compliance_reasoning': Detailed explanation of the compliance determination
- 'compliance': Simple compliance indicator (see below)

## Compliance Indicators

For the compliance field, use these simple indicators:
- "✓" - Employee is compliant (paid rate meets or exceeds Davis-Bacon requirement)
- "✗" - Employee is non-compliant (paid rate is below Davis-Bacon requirement)
- "?" - Uncertain compliance status (requires additional review)

## Compliance Reasoning Requirements

The 'compliance_reasoning' field must provide a clear explanation that includes:
- The basis for the Davis-Bacon classification selection
- The calculation showing Davis-Bacon rate vs. paid rate
- Any special circumstances or assumptions made
- For uncertain cases, specific reasons why additional review is needed

## When to Use "Uncertain" Status

Mark compliance as uncertain ("?") when you encounter situations that require additional review, such as:
- The employee's job title doesn't clearly map to a Davis-Bacon classification
- The employee might be a union member with different wage requirements
- The employee could be an apprentice subject to different wage scales
- Missing or unclear data prevents definitive determination

## Sample Outputs
If you are able to parse the payroll file and perform the analysis, respond with JSON structured like this:
```json
{
  "success": true,
  "employee_name": "John Smith",
  "title": "Electrician",
  "davis_bacon_classification": "Electrician",
  "davis_bacon_base_rate": 32.50,
  "davis_bacon_fringe_rate": 13.00,
  "davis_bacon_total_rate": 45.50,
  "overtime_rate": 68.25,
  "paid_rate": 42.00,
  "compliance_reasoning": "Employee classified as Electrician per Davis-Bacon schedule. Required rate is $32.50 base + $13.00 fringe = $45.50 total. Employee paid $42.00 base rate, which is $3.50 below requirement.",
  "compliance": "✗"
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
- Ensure that your calculation is based on the base hourly rate only, excluding any overtime or premium pay
- The 'overtime_rate' field should contain the Davis-Bacon overtime rate (typically 1.5x the prevailing wage) when applicable, or null if not applicable
- Provide thorough reasoning for each compliance determination to support audit trails and review processes
- - The paid rate is *not* the overtime rate. Only use the base hourly rate for compliance comparisons - *do not* "blend" or "weigh" the overtime and regular/base wages - use *only* the base wages.