import { LEGAL_FOOTER_DISCLAIMER } from "./legalConstants.js";

export function LegalStickyFooter() {
  return (
    <footer className="legal-sticky-footer" role="contentinfo">
      {LEGAL_FOOTER_DISCLAIMER}
    </footer>
  );
}
