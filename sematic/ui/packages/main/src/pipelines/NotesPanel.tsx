import { Box, Stack, TextField, useTheme } from "@mui/material";
import { Note, Run, User } from "@sematic/common/src/Models";
import {
  KeyboardEvent,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState
} from "react";
import { UserContext } from "src/appContext";
import { NoteView } from "src/components/Notes";
import { ExtractContextType } from "src/components/utils/typings";
import { usePipelinePanelsContext, usePipelineRunContext } from "src/hooks/pipelineHooks";
import { NoteCreatePayload, NoteListPayload } from "src/Payloads";
import PipelineRunViewContext from "src/pipelines/PipelineRunViewContext";
import { fetchJSON } from "src/utils";
import PipelinePanelsContext from "src/pipelines/PipelinePanelsContext";

export default function NotesPanel() {
  const theme = useTheme();
  const { user } = useContext(UserContext);

  const { rootRun } 
    = usePipelineRunContext() as ExtractContextType<typeof PipelineRunViewContext> & {
      rootRun: Run
  };
  const { selectedRun } = usePipelinePanelsContext() as ExtractContextType<typeof PipelinePanelsContext> & {
    selectedRun: Run
  };


  const calculatorPath = useMemo(
    () => rootRun.calculator_path,
    [rootRun.calculator_path]
  );

  const anonymousUser: User = useMemo(() => ({
    id: "",
    email: "anonymous@acme.com",
    first_name: "Anonymous",
    last_name: null,
    avatar_url: null,
    api_key: null,
  }), []);

  const [notes, setNotes] = useState<Note[]>([]);
  const [inputDisabled, setInputDisabled] = useState(false);
  const [composedNote, setComposedNote] = useState("");

  useEffect(() => {
    fetchJSON({
      url: "/api/v1/notes?calculator_path=" + calculatorPath,
      apiKey: user?.api_key,
      callback: (payload: NoteListPayload) => {
        setNotes(payload.content);
      },
    });
  }, [calculatorPath, user?.api_key]);

  const submitNote = useCallback(
    (event: KeyboardEvent) => {
      if (event.key !== "Enter" || event.shiftKey) return;
      if (composedNote.length === 0) return;

      setInputDisabled(true);

      fetchJSON({
        url: "/api/v1/notes",
        apiKey: user?.api_key,
        method: "POST",
        body: {
          note: {
            author_id: (user || anonymousUser).email,
            note: composedNote,
            root_id: rootRun.id,
            run_id: selectedRun.id,
          },
        },
        callback: (payload: NoteCreatePayload) => {
          setNotes([...notes, payload.content]);
          setComposedNote("");
          setInputDisabled(false);
        },
      });
    },
    [composedNote, rootRun, selectedRun, notes, anonymousUser, user]
  );

  const bottomRef = useRef<null | HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView();
  }, [notes]);

  return (
    <Box
      sx={{
        gridColumn: 3,
        gridRow: 2,
        borderLeft: 1,
        borderColor: theme.palette.grey[200],
        display: "grid",
        gridTemplateRows: "1fr auto",
        overflowY: "auto",
      }}
    >
      <Box
        sx={{
          gridRow: 1,
          display: "grid",
          gridTemplateRows: "1fr auto",
          borderBottom: 1,
          borderColor: theme.palette.grey[200],
          overflowY: "auto",
        }}
        id="notesList"
      >
        <Box sx={{ gridRow: 1 }}></Box>
        <Box sx={{ gridRow: 2, display: "flex", flexDirection: "column" }}>
          <Box sx={{ display: "grid", gridTemplateRows: "1fr auto" }}>
            <Box sx={{ gridRow: 1 }}></Box>

            <Stack sx={{ gridRow: 2 }}>
              {notes.map((note, idx) => (
                <NoteView
                  note={note}
                  key={idx}
                  author={note.user || anonymousUser}
                />
              ))}
            </Stack>
            <div ref={bottomRef} />
          </Box>
        </Box>
      </Box>
      <Box
        sx={{
          gridRow: 2,
          padding: 1,
          paddingBottom: 3,
        }}
      >
        <TextField
          sx={{ width: "100%", backgroundColor: "#ffffff" }}
          id="filled-textarea"
          label="Add a note"
          placeholder="Your note..."
          multiline
          variant="standard"
          onKeyUp={submitNote}
          onChange={(e) => setComposedNote(e.target.value)}
          disabled={inputDisabled}
          value={composedNote}
        />
      </Box>
    </Box>
  );
}
