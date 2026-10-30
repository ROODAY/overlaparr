import type { EditionCmp, FindingBadges, FindingKind, Quality } from "../api/types";
import { Badge, type BadgeVariant } from "./Badge";

/** [chip text, variant, tooltip]: the text names the dimension so it reads on its own. */
const QUALITY: Record<Quality, [string, BadgeVariant, string]> = {
  better: ["Better elsewhere", "good", "Best quality on other servers is better than yours"],
  same: ["Same quality", "good", "Best quality on other servers matches yours"],
  worse: ["Worse elsewhere", "bad", "Best quality on other servers is worse than yours"],
  unknown: ["Quality unknown", "neutral", "Quality couldn't be compared"],
};

const EDITION: Record<EditionCmp, [string, BadgeVariant, string]> = {
  same: ["Same edition", "good", "Other servers have the same edition as yours"],
  different: ["Different edition", "bad", "Other servers have a different edition from yours"],
  unknown: ["Edition unknown", "neutral", "Edition couldn't be compared"],
};

/** Difference badges: coverage, quality, edition and match method. */
export function DiffBadges({ badges, kind }: { badges: FindingBadges; kind?: FindingKind }) {
  const [qualityText, qualityVariant, qualityTitle] = QUALITY[badges.best_quality];
  const [editionText, editionVariant, editionTitle] = EDITION[badges.edition];
  const showEdition = !(badges.edition === "unknown" && kind === "series");
  return (
    <span className="inline-flex flex-wrap gap-1">
      {badges.coverage === "full" ? (
        <Badge variant="good" title="Other servers have everything you have">
          Full coverage
        </Badge>
      ) : (
        <Badge
          variant="caution"
          title={`Other servers have ${badges.covered_count} of your ${badges.relevant_count}`}
        >
          Partial {badges.covered_count}/{badges.relevant_count}
        </Badge>
      )}
      <Badge variant={qualityVariant} title={qualityTitle}>
        {qualityText}
      </Badge>
      {showEdition && (
        <Badge variant={editionVariant} title={editionTitle}>
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
