-- update total_amount on quantity, unit_amount changes for credit line


CREATE OR REPLACE FUNCTION set_credit_line_total_amount() RETURNS TRIGGER AS $$
    BEGIN
        NEW.total_amount = NEW.quantity * NEW.unit_amount;
        RETURN NEW;
    END;
$$ LANGUAGE plpgsql;


DROP TRIGGER IF EXISTS set_credit_line_amount_trg ON invoicing_creditline;
CREATE TRIGGER set_credit_line_amount_trg
    BEFORE INSERT OR UPDATE OF quantity, unit_amount ON invoicing_creditline
    FOR EACH ROW
    EXECUTE PROCEDURE set_credit_line_total_amount();


-- update credit total_amount, assigned_amount & remaining_amount on line amount/assignment changes

CREATE OR REPLACE FUNCTION set_credit_amounts() RETURNS TRIGGER AS $$
    DECLARE
        credit_ids integer[];
        error_ids integer[];
    BEGIN
        IF TG_OP = 'INSERT' THEN
            credit_ids := ARRAY[NEW.credit_id];
        ELSIF TG_OP = 'DELETE' THEN
            credit_ids := ARRAY[OLD.credit_id];
        ELSIF TG_OP = 'UPDATE' THEN
            credit_ids := ARRAY[NEW.credit_id, OLD.credit_id];
        END IF;

        IF TG_TABLE_NAME = 'invoicing_creditline' THEN
            EXECUTE 'UPDATE ' || substring(TG_TABLE_NAME for length(TG_TABLE_NAME) - 4) || ' i
            SET total_amount = COALESCE(
                (
                    SELECT SUM(l.total_amount)
                    FROM ' || TG_TABLE_NAME || ' l
                    WHERE l.credit_id = i.id
                ), 0
            )
            WHERE id = ANY($1);' USING credit_ids;
        END IF;

        IF TG_TABLE_NAME = 'invoicing_creditassignment' THEN
            EXECUTE 'UPDATE invoicing_credit c
            SET assigned_amount = COALESCE(
                (
                    SELECT SUM(p.amount)
                    FROM invoicing_creditassignment p
                    WHERE p.credit_id = c.id
                ), 0
            )
            WHERE id = ANY($1);' USING credit_ids;
        END IF;

        EXECUTE 'UPDATE invoicing_credit
        SET remaining_amount = total_amount - assigned_amount
        WHERE id = ANY($1);' USING credit_ids;

        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        ELSE
            RETURN NEW;
        END IF;
    END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS set_credit_line_trg ON invoicing_creditline;
CREATE TRIGGER set_credit_line_trg
    AFTER INSERT OR UPDATE OF total_amount, credit_id OR DELETE ON invoicing_creditline
    FOR EACH ROW
    EXECUTE PROCEDURE set_credit_amounts();


DROP TRIGGER IF EXISTS set_credit_creditassignment_trg ON invoicing_creditassignment;
CREATE TRIGGER set_credit_creditassignment_trg
    AFTER INSERT OR UPDATE OF amount OR DELETE ON invoicing_creditassignment
    FOR EACH ROW
    EXECUTE PROCEDURE set_credit_amounts();
