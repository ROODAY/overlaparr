import type { EditionCmp, FindingBadges, FindingKind, Quality } from "../api/types";
import { Badge, type BadgeVariant } from "./Badge";

const QUALITY: Record<Quality, [string, BadgeVariant]> = {
  better: ["Better", "good"],
  same: ["Same", "good"],
  worse: ["Worse", "bad"],
  unknown: ["Unknown", "neutral"],
};

const EDITION: Record<EditionCmp, [string, BadgeVariant]> = {
  same: ["Same", "good"],
  different: ["Different", "bad"],
  unknown: ["Unknown", "neutral"],
};

/** Difference badges: coverage, quality, edition and match method. */
export function DiffBadges({ badges, kind }: { badges: FindingBadges; kind?: FindingKind }) {
  const [qualityText, qualityVariant] = QUALITY[badges.best_quality];
  const [editionText, editionVariant] = EDITION[badges.edition];
  const showEdition = !(badges.edition === "unknown" && kind === "series");
  return (
    <span className="inline-flex flex-wrap gap-1">
      {badges.coverage === "full" ? (
        <Badge variant="good" title="Coverage">
          Full
        </Badge>
      ) : (
        <Badge variant="caution" title="Coverage">
          Partial {badges.covered_count}/{badges.relevant_count}
        </Badge>
      )}
      <Badge variant={qualityVariant} title={`Quality elsewhere vs yours: ${qualityText}`}>
        {qualityText}
      </Badge>
      {showEdition && (
        <Badge variant={editionVariant} title={`Edition: ${editionText}`}>
          {editionText}
        </Badge>
      )}
      {badges.has_number_matches && (
        <Badge variant="caution" title="Some episodes matched by season/episode number only">
          Matched by number
        </Badge>
      )}
    </span>
  );
}
