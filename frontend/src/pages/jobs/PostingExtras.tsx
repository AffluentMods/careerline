// SPDX-License-Identifier: Apache-2.0
import type { PostingDetail } from "../../lib/discovery";
import { InterviewCard } from "../interview/InterviewPages";
import { MaterialsCard } from "../materials/MaterialsCard";

/** Per-posting tools added by later phases: tailored materials and interview prep. */
export function PostingExtras({ posting }: { posting: PostingDetail }) {
  return (
    <>
      <MaterialsCard postingId={posting.id} />
      <InterviewCard postingId={posting.id} />
    </>
  );
}
