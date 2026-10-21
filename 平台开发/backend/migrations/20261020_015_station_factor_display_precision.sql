-- Preserve complete measured values while presenting turbidity and conductivity
-- with the approved minimum of two decimal places across protocol aliases.
UPDATE monitoring_factor_definitions
SET decimal_precision = 2
WHERE business_metric IN ('turbidity', 'conductivity')
  AND decimal_precision < 2;
