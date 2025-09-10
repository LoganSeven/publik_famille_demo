-- update total_amount on quantity, unit_amount changes for draft invoice line & invoice line


CREATE OR REPLACE FUNCTION set_invoice_line_total_amount() RETURNS TRIGGER AS $$
    BEGIN
        NEW.total_amount = NEW.quantity * NEW.unit_amount;
        RETURN NEW;
    END;
$$ LANGUAGE plpgsql;


DROP TRIGGER IF EXISTS set_draftinvoice_line_amount_trg ON invoicing_draftinvoiceline;
CREATE TRIGGER set_draftinvoice_line_amount_trg
    BEFORE INSERT OR UPDATE OF quantity, unit_amount ON invoicing_draftinvoiceline
    FOR EACH ROW
    EXECUTE PROCEDURE set_invoice_line_total_amount();


DROP TRIGGER IF EXISTS set_invoice_line_amount_trg ON invoicing_invoiceline;
CREATE TRIGGER set_invoice_line_amount_trg
    BEFORE INSERT OR UPDATE OF quantity, unit_amount ON invoicing_invoiceline
    FOR EACH ROW
    EXECUTE PROCEDURE set_invoice_line_total_amount();


-- update line paid_amount & remaining_amount on invoice line payment change


CREATE OR REPLACE FUNCTION set_invoice_line_computed_amounts() RETURNS TRIGGER AS $$
    DECLARE
        line_ids integer[];
        error_ids integer[];
    BEGIN
        IF TG_OP = 'INSERT' THEN
            line_ids := ARRAY[NEW.line_id];
        ELSIF TG_OP = 'DELETE' THEN
            line_ids := ARRAY[OLD.line_id];
        ELSIF TG_OP = 'UPDATE' THEN
            line_ids := ARRAY[NEW.line_id, OLD.line_id];
        END IF;

        EXECUTE 'UPDATE invoicing_invoiceline l
        SET paid_amount = COALESCE(
            (
                SELECT SUM(p.amount)
                FROM invoicing_invoicelinepayment p
                WHERE p.line_id = l.id
            ), 0
        )
        WHERE id = ANY($1);' USING line_ids;

        EXECUTE 'UPDATE invoicing_invoiceline
        SET remaining_amount = total_amount - paid_amount
        WHERE id = ANY($1);' USING line_ids;

        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        ELSE
            RETURN NEW;
        END IF;
    END;
$$ LANGUAGE plpgsql;


DROP TRIGGER IF EXISTS set_invoice_invoicelinepayment_trg ON invoicing_invoicelinepayment;
CREATE TRIGGER set_invoice_invoicelinepayment_trg
    AFTER INSERT OR UPDATE OF amount OR DELETE ON invoicing_invoicelinepayment
    FOR EACH ROW
    EXECUTE PROCEDURE set_invoice_line_computed_amounts();


-- update line remaining_amount on total_amout update


CREATE OR REPLACE FUNCTION set_invoice_line_remaining_amount() RETURNS TRIGGER AS $$
    BEGIN
        NEW.remaining_amount = NEW.total_amount - NEW.paid_amount;
        RETURN NEW;
    END;
$$ LANGUAGE plpgsql;


DROP TRIGGER IF EXISTS set_invoice_line_remaining_amount_trg ON invoicing_invoiceline;
CREATE TRIGGER set_invoice_line_remaining_amount_trg
    BEFORE INSERT OR UPDATE OF total_amount, paid_amount ON invoicing_invoiceline
    FOR EACH ROW
    EXECUTE PROCEDURE set_invoice_line_remaining_amount();


-- update invoice total_amount, paid_amount & remaining_amount on line amount changes


CREATE OR REPLACE FUNCTION set_invoice_amounts() RETURNS TRIGGER AS $$
    DECLARE
        invoice_ids integer[];
        error_ids integer[];
    BEGIN
        IF TG_OP = 'INSERT' THEN
            invoice_ids := ARRAY[NEW.invoice_id];
        ELSIF TG_OP = 'DELETE' THEN
            invoice_ids := ARRAY[OLD.invoice_id];
        ELSIF TG_OP = 'UPDATE' THEN
            invoice_ids := ARRAY[NEW.invoice_id, OLD.invoice_id];
        END IF;

        EXECUTE 'UPDATE ' || substring(TG_TABLE_NAME for length(TG_TABLE_NAME) - 4) || ' i
        SET total_amount = COALESCE(
            (
                SELECT SUM(l.total_amount)
                FROM ' || TG_TABLE_NAME || ' l
                WHERE l.invoice_id = i.id
            ), 0
        )
        WHERE id = ANY($1);' USING invoice_ids;

        IF TG_TABLE_NAME = 'invoicing_invoiceline' THEN
            EXECUTE 'UPDATE invoicing_invoice i
            SET paid_amount = COALESCE(
                (
                    SELECT SUM(l.paid_amount)
                    FROM invoicing_invoiceline l
                    WHERE l.invoice_id = i.id
                ), 0
            )
            WHERE id = ANY($1);' USING invoice_ids;
            EXECUTE 'UPDATE invoicing_invoice
            SET remaining_amount = total_amount - paid_amount
            WHERE id = ANY($1);' USING invoice_ids;
        END IF;

        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        ELSE
            RETURN NEW;
        END IF;
    END;
$$ LANGUAGE plpgsql;


DROP TRIGGER IF EXISTS set_draftinvoice_line_trg ON invoicing_draftinvoiceline;
CREATE TRIGGER set_draftinvoice_line_trg
    AFTER INSERT OR UPDATE OF total_amount, invoice_id OR DELETE ON invoicing_draftinvoiceline
    FOR EACH ROW
    EXECUTE PROCEDURE set_invoice_amounts();


DROP TRIGGER IF EXISTS set_invoice_line_trg ON invoicing_invoiceline;
CREATE TRIGGER set_invoice_line_trg
    AFTER INSERT OR UPDATE OF total_amount, paid_amount, remaining_amount, invoice_id OR DELETE ON invoicing_invoiceline
    FOR EACH ROW
    EXECUTE PROCEDURE set_invoice_amounts();


-- old triggers and function
DROP TRIGGER IF EXISTS set_invoice_invoicepayment_trg ON invoicing_invoicepayment;
DROP FUNCTION IF EXISTS set_invoice_line_amount;
